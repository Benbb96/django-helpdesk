"""
django-helpdesk - A Django powered ticket tracker for small enterprise.

(c) Copyright 2008 Jutda. All Rights Reserved. See LICENSE for details.

forms.py - Definitions of newforms-based forms for creating and maintaining
           tickets.
"""
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django import forms
from django.conf import settings
from django.utils.translation import ugettext_lazy as _
from django.contrib.auth import get_user_model
from django.utils import timezone
from django_select2.forms import ModelSelect2MultipleWidget, ModelSelect2Widget

from helpdesk.lib import send_templated_mail, safe_template_context, process_attachments, get_assignable_users
from helpdesk.models import Ticket, Queue, FollowUp, IgnoreEmail, TicketCC, CustomField, TicketCustomFieldValue, \
    TicketDependency, FeedbackSurvey, GenericIncident
from helpdesk import settings as helpdesk_settings
from phonenumber_field.formfields import PhoneNumberField
from phonenumber_field.widgets import PhoneNumberInternationalFallbackWidget

from base.fields import CustomDateTimeField, CustomTinyMCE
from base.widgets import MyModelSelect2Widget
from base.models import get_technical_service
from sphinx.models import Customer, Site, CustomerProducts

User = get_user_model()

CUSTOMFIELD_TO_FIELD_DICT = {
    # Store the immediate equivalences here
    'boolean': forms.BooleanField,
    'date': forms.DateField,
    'time': forms.TimeField,
    'datetime': forms.DateTimeField,
    'email': forms.EmailField,
    'url': forms.URLField,
    'ipaddress': forms.GenericIPAddressField,
    'slug': forms.SlugField,
}


class CustomFieldMixin(object):
    """
    Mixin that provides a method to turn CustomFields into an actual field
    """

    def customfield_to_field(self, field, instanceargs):
        # if-elif branches start with special cases
        if field.data_type == 'varchar':
            fieldclass = forms.CharField
            instanceargs['max_length'] = field.max_length
        elif field.data_type == 'text':
            fieldclass = forms.CharField
            instanceargs['widget'] = forms.Textarea
            instanceargs['max_length'] = field.max_length
        elif field.data_type == 'integer':
            fieldclass = forms.IntegerField
        elif field.data_type == 'decimal':
            fieldclass = forms.DecimalField
            instanceargs['decimal_places'] = field.decimal_places
            instanceargs['max_digits'] = field.max_length
        elif field.data_type == 'list':
            fieldclass = forms.ChoiceField
            choices = field.choices_as_array
            if field.empty_selection_list:
                choices.insert(0, ('', '---------'))
            instanceargs['choices'] = choices
        else:
            # Try to use the immediate equivalences dictionary
            try:
                fieldclass = CUSTOMFIELD_TO_FIELD_DICT[field.data_type]
            except KeyError:
                # The data_type was not found anywhere
                raise NameError("Unrecognized data_type %s" % field.data_type)

        self.fields['custom_%s' % field.name] = fieldclass(**instanceargs)


class PhoenixTicketForm(forms.Form):
    """ Add special fields for Phoenix """
    link_open = forms.URLField(
        widget=forms.HiddenInput()
    )

    customer_contact = forms.ModelChoiceField(
        label='Utilisateur client',
        queryset=User.objects.filter(is_active=True),
        widget=ModelSelect2Widget(
            search_fields=['username__icontains', 'first_name__icontains', 'last_name__icontains', 'email__icontains'],
            attrs={'style': 'width: 100%'}
        ),
        required=False
    )

    customer = forms.ModelChoiceField(
        label='Client',
        queryset=Customer.objects.all(),
        widget=ModelSelect2Widget(
            search_fields=['group__name__icontains'],
            dependent_fields={'site': 'site', 'customer_product': 'site__customerproducts'},
            attrs={'style': 'width: 100%', 'data-minimum-input-length': 0}
        ),
        required=False
    )

    site = forms.ModelChoiceField(
        label='Site',
        queryset=Site.objects.all(),
        widget=ModelSelect2Widget(
            search_fields=['customer__group__name__icontains', 'name__icontains'],
            dependent_fields={
                'customer': 'customer',
                'customer_product': ('customerproducts', 'proxy_customer_products')
            },
            attrs={'style': 'width: 100%', 'data-minimum-input-length': 0}
        ),
        required=False
    )

    customer_product = forms.ModelChoiceField(
        label='Produit client',
        queryset=CustomerProducts.objects.filter(termination_date__isnull=True),
        widget=ModelSelect2Widget(
            search_fields=[
                'site__customer__group__name__icontains', 'site__name__icontains',
                'comment__icontains', 'product__name__icontains'
            ],
            dependent_fields={'site': ('site', 'using_sites'), 'customer': 'site__customer'},
            attrs={'style': 'width: 100%', 'data-minimum-input-length': 0}
        ),
        required=False
    )

    generic_incident = forms.ModelChoiceField(
        label='Incident générique lié',
        queryset=GenericIncident.objects.all(),
        widget=MyModelSelect2Widget,
        required=False
    )


class InformationTicketForm(forms.ModelForm):
    class Meta:
        model = Ticket
        fields = ('category', 'type', 'billing')
        widgets = {
            'category': forms.Select(attrs={'class': 'form-control'}),
            'type': forms.Select(attrs={'class': 'form-control'}),
            'billing': forms.Select(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Remove empty label for required fields
        if self.instance.category:
            self.fields['category'].empty_label = None
        if self.instance.type:
            self.fields['type'].empty_label = None


class EditTicketForm(CustomFieldMixin, PhoenixTicketForm, forms.ModelForm):
    due_date = CustomDateTimeField(required=False)
    link_open = forms.URLField(
        label='Lien ouverture du ticket',
        required=False,
        widget=forms.URLInput(attrs={'palceholder': 'https://phoenix.ipexia.com/...'})
    )
    assigned_to = assigned_to = forms.ModelChoiceField(
        queryset=get_assignable_users(),
        widget=forms.Select(attrs={'class': 'form-control'}),
        required=False,
        label=_('Case owner'),
    )

    class Meta:
        model = Ticket
        exclude = (
            'created', 'modified', 'resolved', 'closed', 'status',
            'on_hold', 'last_escalation', 'merged_to'
        )
        widgets = {
            'link_open': forms.URLInput(),
            'description': CustomTinyMCE(),
            'resolution': CustomTinyMCE(),
        }

    def __init__(self, *args, **kwargs):
        """
        Add any custom fields that are defined to the form
        """
        super(EditTicketForm, self).__init__(*args, **kwargs)

        for field in CustomField.objects.all():
            try:
                current_value = TicketCustomFieldValue.objects.get(ticket=self.instance, field=field)
                initial_value = current_value.value
            except TicketCustomFieldValue.DoesNotExist:
                initial_value = None
            instanceargs = {
                'label': field.label,
                'help_text': field.help_text,
                'required': field.required,
                'initial': initial_value,
            }

            self.customfield_to_field(field, instanceargs)

    def save(self, *args, **kwargs):

        for field, value in self.cleaned_data.items():
            if field.startswith('custom_'):
                field_name = field.replace('custom_', '', 1)
                customfield = CustomField.objects.get(name=field_name)
                try:
                    cfv = TicketCustomFieldValue.objects.get(ticket=self.instance, field=customfield)
                except ObjectDoesNotExist:
                    cfv = TicketCustomFieldValue(ticket=self.instance, field=customfield)
                cfv.value = value
                cfv.save()

        return super(EditTicketForm, self).save(*args, **kwargs)


class CreateFollowUpForm(forms.ModelForm):
    toggle_ticket_status = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={'class': 'flat'})
    )

    class Meta:
        model = FollowUp
        fields = ('comment',)
        widgets = {
            'comment': CustomTinyMCE()
        }

    def clean_comment(self):
        """ Check that comment is not empty """
        comment = self.cleaned_data.get('comment')
        if not comment:
            raise ValidationError('Merci de saisir votre réponse dans le champ texte.')
        return comment


class EditFollowUpForm(forms.ModelForm):

    class Meta:
        model = FollowUp
        exclude = ('date', 'user')
        labels = {
            'ticket': _("Reassign ticket:")
        }
        widgets = {
            'ticket': MyModelSelect2Widget(
                queryset=Ticket.objects.opened(),
                search_fields=['title__icontains', 'id__iexact']
            ),
            'comment': CustomTinyMCE()
        }


class AbstractTicketForm(CustomFieldMixin, forms.Form):
    """
    Contain all the common code and fields between "TicketForm" and
    "PublicTicketForm". This Form is not intended to be used directly.
    """
    queue = forms.ModelChoiceField(
        queryset=Queue.objects.all(),
        widget=forms.Select(attrs={'class': 'form-control'}),
        label=_('Queue'),
        required=True,
    )

    title = forms.CharField(
        max_length=100,
        required=True,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
        label=_('Summary of the problem'),
    )

    body = forms.CharField(
        widget=CustomTinyMCE(),
        label=_('Description of your issue'),
        required=True,
        help_text=_('Please be as descriptive as possible and include all details'),
    )

    priority = forms.ChoiceField(
        widget=forms.Select(attrs={'class': 'form-control'}),
        choices=Ticket.PRIORITY_CHOICES,
        required=True,
        initial='3',
        label=_('Priority'),
        help_text=_("Please select a priority carefully. If unsure, leave it as '3'."),
    )

    due_date = CustomDateTimeField(
        required=False,
        label=_('Due on'),
        form_control=True
    )

    attachment = forms.FileField(
        required=False,
        label=_('Attach File'),
        help_text=_('You can attach a file such as a document or screenshot to this ticket.'),
    )

    def _add_form_custom_fields(self, staff_only_filter=None):
        if staff_only_filter is None:
            queryset = CustomField.objects.all()
        else:
            queryset = CustomField.objects.filter(staff_only=staff_only_filter)

        for field in queryset:
            instanceargs = {
                'label': field.label,
                'help_text': field.help_text,
                'required': field.required,
            }

            self.customfield_to_field(field, instanceargs)

    def _create_ticket(self):
        queue = self.cleaned_data.get('queue')
        ticket = Ticket(
            queue=queue,
            title=self.cleaned_data.get('title'),
            description=self.cleaned_data.get('body'),
            submitter_email=self.cleaned_data['submitter_email'],
            created=timezone.now(),
            status=Ticket.OPEN_STATUS,
            link_open=self.cleaned_data.get('link_open'),
            customer=self.cleaned_data.get('customer'),
            customer_contact=self.cleaned_data.get('customer_contact'),
            contact_phone_number=self.cleaned_data.get('contact_phone_number'),
            site=self.cleaned_data.get('site'),
            customer_product=self.cleaned_data.get('customer_product'),
            priority=self.cleaned_data.get('priority'),
            due_date=self.cleaned_data.get('due_date'),
            quick_comment=self.cleaned_data.get('quick_comment', ''),
            generic_incident=self.cleaned_data.get('generic_incident'),
        )
        return ticket, queue

    def _create_custom_fields(self, ticket):
        for field, value in self.cleaned_data.items():
            if field.startswith('custom_'):
                field_name = field.replace('custom_', '', 1)
                custom_field = CustomField.objects.get(name=field_name)
                cfv = TicketCustomFieldValue.objects.create(
                    ticket=ticket,
                    field=custom_field,
                    value=value
                )

    def _create_follow_up(self, ticket, title, user=None):
        followup = FollowUp(
            ticket=ticket,
            title=title,
            date=timezone.now(),
            public=True,
            comment=self.cleaned_data['body']
        )
        if user:
            followup.user = user
        return followup

    def _attach_files_to_follow_up(self, followup):
        files = self.cleaned_data['attachment']
        if files:
            files = process_attachments(followup, [files])
        return files

    @staticmethod
    def _send_messages(ticket, queue, followup, files, user=None):
        context = safe_template_context(ticket)
        context['comment'] = followup.comment

        messages_sent_to = []

        if ticket.get_submitter_emails():
            send_templated_mail(
                'newticket_submitter',
                context,
                recipients=ticket.get_submitter_emails(),
                sender=queue.from_address,
                fail_silently=True,
                files=files,
            )
            messages_sent_to += ticket.get_submitter_emails()

        if ticket.assigned_to and \
                ticket.assigned_to != user and \
                ticket.assigned_to.usersettings_helpdesk.settings.get('email_on_ticket_assign', False) and \
                ticket.assigned_to.email and \
                ticket.assigned_to.email not in messages_sent_to:
            send_templated_mail(
                'assigned_owner',
                context,
                recipients=ticket.assigned_to.email,
                sender=queue.from_address,
                fail_silently=True,
                files=files,
            )
            messages_sent_to.append(ticket.assigned_to.email)

        if queue.new_ticket_cc and queue.new_ticket_cc not in messages_sent_to:
            send_templated_mail(
                'newticket_cc',
                context,
                recipients=queue.new_ticket_cc,
                sender=queue.from_address,
                fail_silently=True,
                files=files,
            )
            messages_sent_to.append(queue.new_ticket_cc)

        if queue.updated_ticket_cc and \
                queue.updated_ticket_cc != queue.new_ticket_cc and \
                queue.updated_ticket_cc not in messages_sent_to:
            send_templated_mail(
                'newticket_cc',
                context,
                recipients=queue.updated_ticket_cc,
                sender=queue.from_address,
                fail_silently=True,
                files=files,
            )


class TicketForm(AbstractTicketForm, PhoenixTicketForm):
    """
    Ticket Form creation for registered users.
    """
    submitter_email = forms.EmailField(
        required=False,
        label=_('Submitter E-Mail Address'),
        widget=forms.TextInput(attrs={'class': 'form-control submitter-email-autocomplete', 'autocomplete': 'off'}),
        help_text=_('This e-mail address will receive copies of all public updates to this ticket.'),
    )

    contact_phone_number = PhoneNumberField(
        max_length=20,
        label='Numéro de téléphone',
        required=False,
        widget=PhoneNumberInternationalFallbackWidget(attrs={'class': 'form-control'}),
        help_text='Numéro de téléphone de la personne à contacter.'
    )
    update_phone_number = forms.BooleanField(
        label='Mettre à jour le numéro de téléphone sur le contact client ?',
        required=False,
        widget=forms.CheckboxInput(attrs={'class': 'flat'})
    )

    assigned_to = forms.ModelChoiceField(
        queryset=User.objects.none(),
        widget=forms.Select(attrs={'class': 'form-control'}),
        required=False,
        label=_('Case owner'),
        help_text=_('If you select an owner other than yourself, they\'ll be '
                    'e-mailed details of this ticket immediately.'),
    )
    quick_comment = forms.CharField(
        label='Commentaire rapide',
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'maxlength': '200'})
    )

    def __init__(self, user, edit_contextual_fields=False, *args, **kwargs):
        """
        Prepare the form based on if the user is staff or not.
        """
        super(TicketForm, self).__init__(*args, **kwargs)

        # Hide contextual fields by default (it is only possible on the ticket view page)
        if not edit_contextual_fields:
            self.fields['customer_contact'].widget = forms.HiddenInput()
            self.fields['customer'].widget = forms.HiddenInput()
            self.fields['site'].widget = forms.HiddenInput()
            self.fields['customer_product'].widget = forms.HiddenInput()
        else:
            # Remove the body field that is causing conflict with the tinymce js depency in ticket detail page
            self.fields.pop('body')

        if user.employee.is_ipexia_member:
            self.fields['assigned_to'].queryset = get_assignable_users()
            if helpdesk_settings.HELPDESK_CREATE_TICKET_HIDE_ASSIGNED_TO:
                self.fields['assigned_to'].widget = forms.HiddenInput()
        else:
            # Hide some fields
            self.fields.pop('quick_comment')
            self.fields.pop('generic_incident')
            self.fields['customer_contact'].widget = forms.HiddenInput()
            self.fields['priority'].widget = forms.HiddenInput()
            self.fields['assigned_to'].widget = forms.HiddenInput()
            # Set directly the customer if the employee is in only one company
            user_customers = Customer.objects.filter(group__user=user)
            # Automatically set the customer if there is only one
            if len(user_customers) == 1:
                self.initial['customer'] = user_customers.get()
            # Or present a select in order to choose for which the user is opening the ticket
            elif len(user_customers) > 1:
                self.fields['customer'] = forms.ModelChoiceField(
                    label='Client concerné',
                    required=True,
                    queryset=user_customers,
                    widget=forms.Select(attrs={'class': 'form-control'})
                )
            # Set customer contact
            self.initial['customer_contact'] = user
            # Set initial customer phone number and make it mandatory
            self.initial['contact_phone_number'] = user.employee.phone_number
            self.fields['contact_phone_number'].required = True
            self.fields['update_phone_number'].label = 'Utiliser pour mettre à jour mon numéro de téléphone ' \
                                                        'sur mon profil ?'
            # Set initial submitter email
            if user.email:
                self.initial['submitter_email'] = user.email
                self.fields['submitter_email'].widget = forms.HiddenInput()

        # Add any custom fields that are defined to the form
        self._add_form_custom_fields()

    def save(self, user):
        """
        Writes and returns a Ticket() object
        """

        ticket, queue = self._create_ticket()
        if self.cleaned_data['assigned_to']:
            ticket.assigned_to = self.cleaned_data['assigned_to']
        ticket.save()

        self._create_custom_fields(ticket)

        if self.cleaned_data['assigned_to']:
            title = _('Ticket Opened & Assigned to %(name)s') % {
                'name': ticket.get_assigned_to or _("<invalid user>")
            }
        else:
            title = _('Ticket Opened')
        followup = self._create_follow_up(ticket, title=title, user=user)
        # Append signature at the bottom of the followup comment if user is from ipexia
        if user.employee.is_ipexia_member:
            followup.append_signature()
        followup.save()

        files = self._attach_files_to_follow_up(followup)
        self._send_messages(
            ticket=ticket,
            queue=queue,
            followup=followup,
            files=files,
            user=user
        )
        return ticket


class PublicTicketForm(AbstractTicketForm):
    """
    Ticket Form creation for all users (public-facing).
    """
    submitter_email = forms.EmailField(
        widget=forms.TextInput(attrs={'class': 'form-control'}),
        required=True,
        label=_('Your E-Mail Address'),
        help_text=_('We will e-mail you when your ticket is updated.'),
    )

    def __init__(self, *args, **kwargs):
        """
        Add any (non-staff) custom fields that are defined to the form
        """
        super(PublicTicketForm, self).__init__(*args, **kwargs)

        if hasattr(settings, 'HELPDESK_PUBLIC_TICKET_QUEUE'):
            self.fields['queue'].widget = forms.HiddenInput()
        if hasattr(settings, 'HELPDESK_PUBLIC_TICKET_PRIORITY'):
            self.fields['priority'].widget = forms.HiddenInput()
        if hasattr(settings, 'HELPDESK_PUBLIC_TICKET_DUE_DATE'):
            self.fields['due_date'].widget = forms.HiddenInput()

        self.fields['queue'].queryset = Queue.objects.filter(allow_public_submission=True)

        self._add_form_custom_fields(False)

    def save(self, user):
        """
        Writes and returns a Ticket() object
        """
        ticket, queue = self._create_ticket()
        if queue.default_owner and not ticket.assigned_to:
            ticket.assigned_to = queue.default_owner
        ticket.save()

        self._create_custom_fields(ticket)

        followup = self._create_follow_up(
            ticket, title=_('Ticket Opened Via Web'), user=user)
        followup.save()

        files = self._attach_files_to_follow_up(followup)
        self._send_messages(
            ticket=ticket,
            queue=queue,
            followup=followup,
            files=files)
        return ticket


class UserSettingsForm(forms.Form):
    login_view_ticketlist = forms.BooleanField(
        label=_('Show Ticket List on Login?'),
        help_text=_('Display the ticket list upon login? Otherwise, the dashboard is shown.'),
        required=False,
    )

    email_on_ticket_change = forms.BooleanField(
        label=_('E-mail me on ticket change?'),
        help_text=_('If you\'re the ticket owner and the ticket is changed via the web by somebody else, do you want to receive an e-mail?'),
        required=False,
    )

    email_on_ticket_assign = forms.BooleanField(
        label=_('E-mail me when assigned a ticket?'),
        help_text=_('If you are assigned a ticket via the web, do you want to receive an e-mail?'),
        required=False,
    )

    tickets_per_page = forms.ChoiceField(
        label=_('Number of tickets to show per page'),
        help_text=_('How many tickets do you want to see on the Ticket List page?'),
        required=False,
        choices=((10, '10'), (25, '25'), (50, '50'), (100, '100')),
    )

    use_email_as_submitter = forms.BooleanField(
        label=_('Use my e-mail address when submitting tickets?'),
        help_text=_('When you submit a ticket, do you want to automatically '
                    'use your e-mail address as the submitter address? You '
                    'can type a different e-mail address when entering the '
                    'ticket if needed, this option only changes the default.'),
        required=False,
    )


class EmailIgnoreForm(forms.ModelForm):

    class Meta:
        model = IgnoreEmail
        exclude = []


class TicketCCForm(forms.ModelForm):
    ''' Adds either an email address or helpdesk user as a CC on a Ticket. Used for processing POST requests. '''

    class Meta:
        model = TicketCC
        exclude = ('ticket',)

    def __init__(self, *args, **kwargs):
        super(TicketCCForm, self).__init__(*args, **kwargs)
        if helpdesk_settings.HELPDESK_STAFF_ONLY_TICKET_CC:
            users = User.objects.filter(is_active=True, is_staff=True).order_by(User.USERNAME_FIELD)
        else:
            users = User.objects.filter(is_active=True).order_by(User.USERNAME_FIELD)
        self.fields['user'].queryset = users

    def clean(self):
        cleaned_data = super().clean()
        email = cleaned_data.get('email', None)
        user = cleaned_data.get('user', None)
        if not email and not user:
            raise ValidationError(_('Please fill an email address or choose a user'))


class TicketCCUserForm(forms.ModelForm):
    ''' Adds a helpdesk user as a CC on a Ticket '''

    def __init__(self, *args, **kwargs):
        super(TicketCCUserForm, self).__init__(*args, **kwargs)
        if helpdesk_settings.HELPDESK_STAFF_ONLY_TICKET_CC:
            users = User.objects.filter(is_active=True, is_staff=True).order_by(User.USERNAME_FIELD)
        else:
            users = User.objects.filter(is_active=True).order_by(User.USERNAME_FIELD)
        self.fields['user'].queryset = users

    class Meta:
        model = TicketCC
        exclude = ('ticket', 'email',)
        widgets = {
            'user': ModelSelect2MultipleWidget(
                model=User,
                search_fields=[
                    'username__icontains', 'first_name__icontains', 'last_name__icontains', 'email__icontains'
                ],
                attrs={'style': 'width: 100%'}
            )
        }


class TicketCCEmailForm(forms.ModelForm):
    ''' Adds an email address as a CC on a Ticket '''

    def __init__(self, *args, **kwargs):
        super(TicketCCEmailForm, self).__init__(*args, **kwargs)

    class Meta:
        model = TicketCC
        exclude = ('ticket', 'user',)
        widgets = {
            'email': forms.TextInput(attrs={'class': 'form-control'})
        }


class TicketDependencyForm(forms.ModelForm):
    ''' Adds a different ticket as a dependency for this Ticket '''

    class Meta:
        model = TicketDependency
        exclude = ('ticket',)
        widgets = {
            'depends_on': forms.Select(attrs={'class': 'form-control'})
        }


class MultipleTicketSelectForm(forms.Form):
    tickets = forms.ModelMultipleChoiceField(
        label='Tickets à fusionner',
        queryset=Ticket.objects.all(),
        widget=ModelSelect2MultipleWidget(
            model=Ticket,
            search_fields=['id__iexact', 'title__icontains'],
            attrs={'style': 'width: 100%', 'data-minimum-input-length': 1}
        )
    )

    def clean_tickets(self):
        tickets = self.cleaned_data.get('tickets')
        if len(tickets) < 2:
            raise ValidationError('Veuillez sélectionner au moins 2 tickets')
        if len(tickets) > 4:
            raise ValidationError('Impossible de fusionner plus de 4 tickets...')
        return tickets


class FeedbackSurveyForm(forms.ModelForm):
    class Meta:
        model = FeedbackSurvey
        exclude = ('ticket', 'author')
        widgets = {
            'message': forms.Textarea(attrs={'class': 'form-control resize-vertical'})
        }


class GenericIncidentForm(forms.ModelForm):
    start_date = CustomDateTimeField(
        label='Date de début',
        form_control=True
    )
    end_date = CustomDateTimeField(
        label='Date de fin',
        required=False,
        form_control=True,
        help_text="Laisser vide pour indiquer que l'IG est en cours."
    )
    class Meta:
        model = GenericIncident
        exclude = ('subscribers',)
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control resize-vertical'}),
            'external_link': forms.TextInput(attrs={'class': 'form-control'}),
            'category': forms.Select(attrs={'class': 'form-control'}),
        }

