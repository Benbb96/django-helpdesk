"""
django-helpdesk - A Django powered ticket tracker for small enterprise.

(c) Copyright 2008 Jutda. All Rights Reserved. See LICENSE for details.

models.py - Model (and hence database) definitions. This is the core of the
            helpdesk structure.
"""

from __future__ import unicode_literals

import datetime

from django.contrib.auth.models import Permission, User
from django.contrib.contenttypes.fields import GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ObjectDoesNotExist
from django.core.validators import MaxValueValidator
from django.db import models
from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from django.utils.functional import cached_property
from django.utils.translation import ugettext_lazy as _, ugettext
import re

from phonenumber_field.modelfields import PhoneNumberField
from six import StringIO
from tinymce import HTMLField

from base.models import SpentTime, AbstractGenericIncident, Notification
from base.utils import office_time_between


class Queue(models.Model):
    """
    A queue is a collection of tickets into what would generally be business
    areas or departments.

    For example, a company may have a queue for each Product they provide, or
    a queue for each of Accounts, Pre-Sales, and Support.

    """

    title = models.CharField(
        _('Title'),
        max_length=100,
    )

    slug = models.SlugField(
        _('Slug'),
        max_length=50,
        unique=True,
        help_text=_('This slug is used when building ticket ID\'s. Once set, '
                    'try not to change it or e-mailing may get messy.'),
    )

    email_address = models.EmailField(
        _('E-Mail Address'),
        blank=True,
        null=True,
        help_text=_('All outgoing e-mails for this queue will use this e-mail '
                    'address. If you use IMAP or POP3, this should be the e-mail '
                    'address for that mailbox.'),
    )

    phone_number = PhoneNumberField('numéro de téléphone', blank=True)

    locale = models.CharField(
        _('Locale'),
        max_length=10,
        blank=True,
        null=True,
        help_text=_('Locale of this queue. All correspondence in this '
                    'queue will be in this language.'),
    )

    allow_public_submission = models.BooleanField(
        _('Allow Public Submission?'),
        blank=True,
        default=False,
        help_text=_('Should this queue be listed on the public submission form?'),
    )

    allow_email_submission = models.BooleanField(
        _('Allow E-Mail Submission?'),
        blank=True,
        default=False,
        help_text=_('Do you want to poll the e-mail box below for new '
                    'tickets?'),
    )

    escalate_days = models.IntegerField(
        _('Escalation Days'),
        blank=True,
        null=True,
        help_text=_('For tickets which are not held, how often do you wish to '
                    'increase their priority? Set to 0 for no escalation.'),
    )

    new_ticket_cc = models.CharField(
        _('New Ticket CC Address'),
        blank=True,
        null=True,
        max_length=200,
        help_text=_('If an e-mail address is entered here, then it will '
                    'receive notification of all new tickets created for this queue. '
                    'Enter a comma between multiple e-mail addresses.'),
    )

    updated_ticket_cc = models.CharField(
        _('Updated Ticket CC Address'),
        blank=True,
        null=True,
        max_length=200,
        help_text=_('If an e-mail address is entered here, then it will '
                    'receive notification of all activity (new tickets, closed '
                    'tickets, updates, reassignments, etc) for this queue. Separate '
                    'multiple addresses with a comma.'),
    )

    email_box_type = models.CharField(
        _('E-Mail Box Type'),
        max_length=5,
        choices=(('pop3', _('POP 3')), ('imap', _('IMAP')), ('local', _('Local Directory'))),
        blank=True,
        null=True,
        help_text=_('E-Mail server type for creating tickets automatically '
                    'from a mailbox - both POP3 and IMAP are supported, as well as '
                    'reading from a local directory.'),
    )

    email_box_host = models.CharField(
        _('E-Mail Hostname'),
        max_length=200,
        blank=True,
        null=True,
        help_text=_('Your e-mail server address - either the domain name or '
                    'IP address. May be "localhost".'),
    )

    email_box_port = models.IntegerField(
        _('E-Mail Port'),
        blank=True,
        null=True,
        help_text=_('Port number to use for accessing e-mail. Default for '
                    'POP3 is "110", and for IMAP is "143". This may differ on some '
                    'servers. Leave it blank to use the defaults.'),
    )

    email_box_ssl = models.BooleanField(
        _('Use SSL for E-Mail?'),
        blank=True,
        default=False,
        help_text=_('Whether to use SSL for IMAP or POP3 - the default ports '
                    'when using SSL are 993 for IMAP and 995 for POP3.'),
    )

    email_box_user = models.CharField(
        _('E-Mail Username'),
        max_length=200,
        blank=True,
        null=True,
        help_text=_('Username for accessing this mailbox.'),
    )

    email_box_pass = models.CharField(
        _('E-Mail Password'),
        max_length=200,
        blank=True,
        null=True,
        help_text=_('Password for the above username'),
    )

    email_box_imap_folder = models.CharField(
        _('IMAP Folder'),
        max_length=100,
        blank=True,
        null=True,
        help_text=_('If using IMAP, what folder do you wish to fetch messages '
                    'from? This allows you to use one IMAP account for multiple '
                    'queues, by filtering messages on your IMAP server into separate '
                    'folders. Default: INBOX.'),
    )

    email_box_local_dir = models.CharField(
        _('E-Mail Local Directory'),
        max_length=200,
        blank=True,
        null=True,
        help_text=_('If using a local directory, what directory path do you '
                    'wish to poll for new email? '
                    'Example: /var/lib/mail/helpdesk/'),
    )

    permission_name = models.CharField(
        _('Django auth permission name'),
        max_length=72,  # based on prepare_permission_name() pre-pending chars to slug
        blank=True,
        null=True,
        editable=False,
        help_text=_('Name used in the django.contrib.auth permission system'),
    )

    email_box_interval = models.IntegerField(
        _('E-Mail Check Interval'),
        help_text=_('How often do you wish to check this mailbox? (in Minutes)'),
        blank=True,
        null=True,
        default='5',
    )

    email_box_last_check = models.DateTimeField(
        blank=True,
        null=True,
        editable=False,
        # This is updated by management/commands/get_mail.py.
    )

    socks_proxy_type = models.CharField(
        _('Socks Proxy Type'),
        max_length=8,
        choices=(('socks4', _('SOCKS4')), ('socks5', _('SOCKS5'))),
        blank=True,
        null=True,
        help_text=_('SOCKS4 or SOCKS5 allows you to proxy your connections through a SOCKS server.'),
    )

    socks_proxy_host = models.GenericIPAddressField(
        _('Socks Proxy Host'),
        blank=True,
        null=True,
        help_text=_('Socks proxy IP address. Default: 127.0.0.1'),
    )

    socks_proxy_port = models.IntegerField(
        _('Socks Proxy Port'),
        blank=True,
        null=True,
        help_text=_('Socks proxy port number. Default: 9150 (default TOR port)'),
    )

    logging_type = models.CharField(
        _('Logging Type'),
        max_length=5,
        choices=(
            ('none', _('None')),
            ('debug', _('Debug')),
            ('info', _('Information')),
            ('warn', _('Warning')),
            ('error', _('Error')),
            ('crit', _('Critical'))
        ),
        blank=True,
        null=True,
        help_text=_('Set the default logging level. All messages at that '
                    'level or above will be logged to the directory set '
                    'below. If no level is set, logging will be disabled.'),
    )

    logging_dir = models.CharField(
        _('Logging Directory'),
        max_length=200,
        blank=True,
        null=True,
        help_text=_('If logging is enabled, what directory should we use to '
                    'store log files for this queue? '
                    'If no directory is set, default to /var/log/helpdesk/'),
    )

    default_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name='default_owner',
        blank=True,
        null=True,
        verbose_name=_('Default owner'),
    )

    def __str__(self):
        return "%s" % self.title

    class Meta:
        ordering = ('title',)
        verbose_name = _('Queue')
        verbose_name_plural = _('Queues')

    def _from_address(self):
        """
        Short property to provide a sender address in SMTP format,
        eg 'Name <email>'. We do this so we can put a simple error message
        in the sender name field, so hopefully the admin can see and fix it.
        """
        if not self.email_address:
            # must check if given in format "Foo <foo@example.com>"
            default_email = re.match(".*<(?P<email>.*@*.)>", settings.DEFAULT_FROM_EMAIL)
            if default_email is not None:
                # already in the right format, so just include it here
                return u'NO QUEUE EMAIL ADDRESS DEFINED %s' % settings.DEFAULT_FROM_EMAIL
            else:
                return u'NO QUEUE EMAIL ADDRESS DEFINED <%s>' % settings.DEFAULT_FROM_EMAIL
        else:
            return u'%s <%s>' % (self.title, self.email_address)
    from_address = property(_from_address)

    def prepare_permission_name(self):
        """Prepare internally the codename for the permission and store it in permission_name.
        :return: The codename that can be used to create a new Permission object.
        """
        # Prepare the permission associated to this Queue
        basename = "queue_access_%s" % self.slug
        self.permission_name = "helpdesk.%s" % basename
        return basename

    def save(self, *args, **kwargs):
        if self.email_box_type == 'imap' and not self.email_box_imap_folder:
            self.email_box_imap_folder = 'INBOX'

        if self.socks_proxy_type:
            if not self.socks_proxy_host:
                self.socks_proxy_host = '127.0.0.1'
            if not self.socks_proxy_port:
                self.socks_proxy_port = 9150
        else:
            self.socks_proxy_host = None
            self.socks_proxy_port = None

        if not self.email_box_port:
            if self.email_box_type == 'imap' and self.email_box_ssl:
                self.email_box_port = 993
            elif self.email_box_type == 'imap' and not self.email_box_ssl:
                self.email_box_port = 143
            elif self.email_box_type == 'pop3' and self.email_box_ssl:
                self.email_box_port = 995
            elif self.email_box_type == 'pop3' and not self.email_box_ssl:
                self.email_box_port = 110

        if not self.id:
            # Prepare the permission codename and the permission
            # (even if they are not needed with the current configuration)
            basename = self.prepare_permission_name()

            Permission.objects.create(
                name=_("Permission for queue: ") + self.title,
                content_type=ContentType.objects.get_for_model(self.__class__),
                codename=basename,
            )

        super(Queue, self).save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        permission_name = self.permission_name
        super(Queue, self).delete(*args, **kwargs)

        # once the Queue is safely deleted, remove the permission (if exists)
        if permission_name:
            try:
                p = Permission.objects.get(codename=permission_name[9:])
                p.delete()
            except ObjectDoesNotExist:
                pass


class TicketCategory(models.Model):
    name = models.CharField('nom', max_length=50, unique=True)

    class Meta:
        verbose_name = 'Catégorie ticket'
        verbose_name_plural = 'Catégories ticket'
        ordering = ('name',)

    def __str__(self):
        return self.name


class GenericIncident(AbstractGenericIncident):
    category = models.ForeignKey(
        TicketCategory,
        verbose_name='catégorie',
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )
    external_link = models.URLField('lien externe', blank=True)
    intervention_report = models.FileField(
        "rapport d'intervention",
        upload_to='intervention_reports/%Y/%m/', blank=True
    )
    followups = GenericRelation('sphinx.GenericInformation', related_query_name='generic_incident')
    subscribers = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        verbose_name='abonnés',
        help_text='List of people who wish to receive updates about this generic incident',
        blank=True
    )
    archived = models.BooleanField(verbose_name='Archivé', default=False)

    class Meta:
        verbose_name = 'Incident Générique'
        verbose_name_plural = 'Incidents Génériques'

    def get_absolute_url(self):
        return reverse('helpdesk:generic_incident_detail', kwargs={'generic_incident_id': self.id})


class TicketType(models.Model):
    name = models.CharField('nom', max_length=50, unique=True)
    mandatory_facturation = models.BooleanField('facturation obligatoire', default=False)

    class Meta:
        verbose_name = 'Type ticket'
        verbose_name_plural = 'Types ticket'
        ordering = ('name',)

    def __str__(self):
        return self.name


class SimpleUserMail(models.Model):
    customer = models.ForeignKey(
        'sphinx.Customer',
        on_delete=models.PROTECT,
        verbose_name='Client',
        null=True,
        blank=True
    )
    email = models.EmailField('mail', unique=True)

    class Meta:
        verbose_name = "Email d'un simple utilisateur"
        verbose_name_plural = "Emails de simples utilisateurs"

    def __str__(self):
        return self.email


class TicketQuerySet(models.QuerySet):
    def opened(self):
        return self.filter(status__in=[Ticket.OPEN_STATUS, Ticket.REOPENED_STATUS])

    def resolved(self):
        return self.filter(status=Ticket.RESOLVED_STATUS)

    def closed(self):
        return self.filter(status__in=[Ticket.CLOSED_STATUS, Ticket.DUPLICATE_STATUS])

    def not_closed(self):
        return self.exclude(status__in=[Ticket.CLOSED_STATUS, Ticket.DUPLICATE_STATUS])

    def on_hold(self):
        return self.filter(on_hold=True)


class Ticket(models.Model):
    """
    To allow a ticket to be entered as quickly as possible, only the
    bare minimum fields are required. These basically allow us to
    sort and manage the ticket. The user can always go back and
    enter more information later.

    A good example of this is when a customer is on the phone, and
    you want to give them a ticket ID as quickly as possible. You can
    enter some basic info, save the ticket, give the customer the ID
    and get off the phone, then add in further detail at a later time
    (once the customer is not on the line).

    Note that assigned_to is optional - unassigned tickets are displayed on
    the dashboard to prompt users to take ownership of them.
    """

    OPEN_STATUS = 1
    REOPENED_STATUS = 2
    RESOLVED_STATUS = 3
    CLOSED_STATUS = 4
    DUPLICATE_STATUS = 5

    STATUS_CHOICES = (
        (OPEN_STATUS, _('Open')),
        (REOPENED_STATUS, _('Reopened')),
        (RESOLVED_STATUS, _('Resolved')),
        (CLOSED_STATUS, _('Closed')),
        (DUPLICATE_STATUS, _('Duplicate')),
    )

    PRIORITY_CHOICES = (
        (1, _('1. Critical')),
        (2, _('2. High')),
        (3, _('3. Normal')),
        (4, _('4. Low')),
        (5, _('5. Very Low')),
    )

    MAINTENANCE_CONTRACT = 1
    TOKENS = 2
    QUOTE = 3
    FREE = 4
    BILLING_WRONGLY = 5

    BILLINGS = (
        (MAINTENANCE_CONTRACT, 'Contrat de maintenance'),
        (TOKENS, 'Carnet de jeton'),
        (QUOTE, 'Devis'),
        (FREE, 'Offert'),
        (BILLING_WRONGLY, 'Facturation à tort')
    )

    title = models.CharField(
        _('Title'),
        max_length=200,
    )

    queue = models.ForeignKey(
        Queue,
        on_delete=models.CASCADE,
        verbose_name=_('Queue'),
    )

    created = models.DateTimeField(
        _('Created'),
        blank=True,
        help_text=_('Date this ticket was first created'),
    )

    modified = models.DateTimeField(
        _('Modified'),
        blank=True,
        help_text=_('Date this ticket was most recently changed.'),
    )

    resolved = models.DateTimeField(
        _('Resolved'),
        null=True,
        blank=True,
        help_text=_('Date this ticket was resolved'),
    )

    closed = models.DateTimeField(
        _('Closed'),
        null=True,
        blank=True,
        help_text=_('Date this ticket was closed'),
    )

    submitter_email = models.EmailField(
        _('Submitter E-Mail'),
        blank=True,
        null=True,
        help_text=_('The submitter will receive an email for all public '
                    'follow-ups left for this task.'),
    )

    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='assigned_to',
        blank=True,
        null=True,
        verbose_name=_('Assigned to'),
    )

    status = models.IntegerField(
        _('Status'),
        choices=STATUS_CHOICES,
        default=OPEN_STATUS,
    )

    category = models.ForeignKey(
        TicketCategory,
        verbose_name='catégorie',
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    type = models.ForeignKey(
        TicketType,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    billing = models.PositiveSmallIntegerField(
        'facturation',
        choices=BILLINGS,
        null=True,
        blank=True
    )

    on_hold = models.BooleanField(
        _('On Hold'),
        blank=True,
        default=False,
        help_text=_('If a ticket is on hold, it will not automatically be escalated.'),
    )

    description = HTMLField(
        _('Description'),
        blank=True,
        help_text=_('The content of the customers query.'),
    )

    resolution = HTMLField(
        _('Resolution'),
        blank=True,
        help_text=_('The resolution provided to the customer by our staff.'),
    )

    priority = models.IntegerField(
        _('Priority'),
        choices=PRIORITY_CHOICES,
        default=3,
        blank=3,
        help_text=_('1 = Highest Priority, 5 = Low Priority'),
    )

    due_date = models.DateTimeField(
        _('Due on'),
        blank=True,
        null=True,
    )

    last_escalation = models.DateTimeField(
        blank=True,
        null=True,
        editable=False,
        help_text=_('The date this ticket was last escalated - updated '
                    'automatically by management/commands/escalate_tickets.py.'),
    )

    link_open = models.URLField(
        max_length=300,
        null=True,
        blank=True,
        help_text="Lien depuis lequel le ticket a été ouvert."
    )

    customer_contact = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name='tickets',
        null=True,
        blank=True
    )

    contact_phone_number = PhoneNumberField('numéro de téléphone', blank=True)

    customer = models.ForeignKey(
        'sphinx.Customer',
        on_delete=models.SET_NULL,
        related_name='tickets',
        null=True,
        blank=True
    )

    site = models.ForeignKey(
        'sphinx.Site',
        on_delete=models.SET_NULL,
        related_name='tickets',
        null=True,
        blank=True
    )

    customer_product = models.ForeignKey(
        'sphinx.CustomerProducts',
        on_delete=models.SET_NULL,
        related_name='tickets',
        null=True,
        blank=True
    )

    quick_comment = models.CharField(
        'commentaire rapide',
        max_length=200,
        blank=True
    )

    time_before_first_answer = models.DurationField(
        'temps avant la première réponse',
        null=True,
        blank=True
    )

    merged_to = models.ForeignKey(
        'self',
        verbose_name='fusionné à',
        related_name='merged_tickets',
        on_delete=models.CASCADE,
        null=True,
        blank=True
    )

    generic_incident = models.ForeignKey(
        GenericIncident,
        verbose_name='inicident générique',
        related_name='tickets',
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    spent_times = GenericRelation(SpentTime, related_name='tickets')

    objects = TicketQuerySet.as_manager()

    def _get_assigned_to(self):
        """ Custom property to allow us to easily print 'Unassigned' if a
        ticket has no owner, or the users name if it's assigned. If the user
        has a full name configured, we use that, otherwise their username. """
        if not self.assigned_to:
            return _('Unassigned')
        else:
            if self.assigned_to.get_full_name():
                return self.assigned_to.get_full_name()
            else:
                return self.assigned_to.get_username()
    get_assigned_to = property(_get_assigned_to)

    def _get_ticket(self):
        """ A user-friendly ticket ID, which is a combination of ticket ID
        and queue slug. This is generally used in e-mail subjects. """

        return u"[%s]" % self.ticket_for_url
    ticket = property(_get_ticket)

    def _get_ticket_for_url(self):
        """ A URL-friendly ticket ID, used in links. """
        return u"%s-%s" % (self.queue.slug, self.id)
    ticket_for_url = property(_get_ticket_for_url)

    def _get_priority_css_class(self):
        """
        Return the boostrap class based on the last followup whether it needs an action or not.
        """
        # Follow ups are already ordered by date desc, so the first is the latest
        last_followup = self.followup_set.first()

        if not last_followup:
            return "danger"
        elif last_followup.public:
            # If last public answer was made by a technical user, it's good
            if last_followup.user and last_followup.user.is_staff:
                return 'success'
            # Else, it must be by the customer, so it requires an action, unless the ticket is closed
            if self.status == self.CLOSED_STATUS:
                return ''
            return "danger"
        # If it is a private followup, then there is no speacial color
        else:
            return ''
    get_priority_css_class = property(_get_priority_css_class)

    def _get_status(self):
        """
        Displays the ticket status, with an "On Hold" message if needed.
        """
        held_msg = ''
        if self.on_hold:
            held_msg = _(' - On Hold')
        dep_msg = ''
        # Stop searching for open dependencies to reduce number of queries on ticket list
        # if not self.can_be_resolved:
        #     dep_msg = _(' - Open dependencies')
        return u'%s%s%s' % (self.get_status_display(), held_msg, dep_msg)
    get_status = property(_get_status)

    def _get_ticket_url(self):
        """
        Returns a publicly-viewable URL for this ticket, used when giving
        a URL to the submitter of a ticket.
        """
        return "%s%s?ticket=%s&email=%s" % (
            settings.SITE_URL,
            reverse('helpdesk:public_view'),
            self.ticket_for_url,
            self.submitter_email
        )
    ticket_url = property(_get_ticket_url)

    def _get_staff_url(self):
        """
        Returns a staff-only URL for this ticket, used when giving a URL to
        a staff member (in emails etc)
        """
        return "%s%s" % (
            settings.SITE_URL,
            reverse('helpdesk:view',
                    args=[self.id])
        )
    staff_url = property(_get_staff_url)

    def _can_be_resolved(self):
        """
        Returns a boolean.
        True = any dependencies are resolved
        False = There are non-resolved dependencies
        """
        open_statuses = (Ticket.OPEN_STATUS, Ticket.REOPENED_STATUS)
        return not self.ticketdependency.filter(depends_on__status__in=open_statuses).exists()
    can_be_resolved = cached_property(_can_be_resolved)

    class Meta:
        get_latest_by = "created"
        ordering = ('id',)
        verbose_name = _('Ticket')
        verbose_name_plural = _('Tickets')

    def __str__(self):
        return '#%s - %s' % (self.id, self.title)

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse('helpdesk:view', args=(self.id,))

    def save(self, *args, **kwargs):
        if not self.id:
            # This is a new ticket as no ID yet exists.
            self.created = timezone.now()

            # Add submitter_email in SimpleUserMail if it didn't exist and no user has this email
            if self.submitter_email and not SimpleUserMail.objects.filter(email=self.submitter_email).exists() \
                    and not User.objects.filter(email=self.submitter_email).exists():
                SimpleUserMail.objects.create(email=self.submitter_email)

        if not self.priority:
            self.priority = 3

        # Update modified date
        self.modified = timezone.now()

        # Update resolved date
        if self.status == self.RESOLVED_STATUS and not self.resolved:
            self.resolved = timezone.now()
            # If closed date was still set, remove it
            if self.closed:
                self.closed = None

        # Update closed date when closing or setting as duplicate
        elif self.status in [self.CLOSED_STATUS, self.DUPLICATE_STATUS] and not self.closed:
            self.closed = timezone.now()

        # If ticket is reopened, remove closed and resolved dates
        elif self.status in [self.OPEN_STATUS, self.REOPENED_STATUS] and (self.closed or self.resolved):
            self.closed = None
            self.resolved = None

        # Update SimpleUserMail when customer is set
        if self.customer and self.submitter_email:
            SimpleUserMail.objects.filter(email=self.submitter_email, customer__isnull=True)\
                .update(customer=self.customer)

        # Set ticket customer if a simple user mail is matching
        if not self.customer \
                and SimpleUserMail.objects.filter(email=self.submitter_email, customer__isnull=False).exists():
            self.customer = SimpleUserMail.objects.get(email=self.submitter_email).customer
            # Note that it will block from removing customer from the ticket if one is matching

        super(Ticket, self).save(*args, **kwargs)

        # Override values onto merged tickets
        self.merged_tickets.update(
            assigned_to=self.assigned_to,
            category=self.category,
            type=self.type,
            billing=self.billing,
            customer=self.customer,
            site=self.site,
            customer_product=self.customer_product,
        )

    @staticmethod
    def queue_and_id_from_query(query):
        # Apply the opposite logic here compared to self._get_ticket_for_url
        # Ensure that queues with '-' in them will work
        parts = query.split('-')
        queue = '-'.join(parts[0:-1])
        return queue, parts[-1]

    def get_submitter_emails(self):
        """ Return customer contact email if it exists and submitter email if different """
        recipients = []
        if self.customer_contact and self.customer_contact.email:
            recipients.append(self.customer_contact.email)
        if self.submitter_email and self.submitter_email not in recipients:
            recipients.append(self.submitter_email)
        return recipients

    def is_email_already_subscribed(self, email):
        """
        Check if email passed in parameter is already attached to the ticket.
        Either by being assigned to it, being the submitter or being in the ticket CC.

        :param str email: user to check
        :rtype: bool
        """
        if self.assigned_to and self.assigned_to.email == email:
            return True

        return self.submitter_email == email or self.ticketcc_set.filter(email=email).exists()

    def is_user_already_subscribed(self, user):
        """
        Check if user passed in parameter already is attached to the ticket.
        Either by being assigned to it, being the submitter or being in the ticket CC.

        :param User user: user to check
        :rtype: bool
        """
        if self.assigned_to == user or self.customer_contact == user or self.ticketcc_set.filter(user=user).exists():
            return True

        return self.is_email_already_subscribed(user.email) if user.email else False

    def add_to_ticketcc_if_not_in(self, email=None, user=None, ticketcc=None):
        """
        Check that given email/user email/ticketcc email is not already present on the ticket
        then and add it to a new ticket CC, or move to this ticket

        :param str email:
        :param User user:
        :param TicketCC ticketcc:
        """
        assert email or user or ticketcc

        if user and not self.is_user_already_subscribed(user):
            self.ticketcc_set.create(user=user, can_view=True, can_update=True)
        elif not self.is_email_already_subscribed(email):
            self.ticketcc_set.create(email=email, can_view=True)
        elif ticketcc and not self.is_email_already_subscribed(ticketcc.email_address):
            # Move ticketcc to the new ticket
            ticketcc.ticket = self
            ticketcc.save(update_fields=['ticket'])

    def is_closed_and_too_old(self):
        """
        :return: True if the ticket is closed since more than 15 days
        :rtype: bool
        """
        ticket_too_old = False
        if self.closed:
            delta_since_closed = timezone.now() - self.closed
            if delta_since_closed.days > 15:
                ticket_too_old = True
        return ticket_too_old

    def calc_time_before_first_answer(self):
        """
        Calculate the time between the creation of the ticket and the first public answer made by a staff user.
        This function shouldn't be used anymore.

        :return: the delta between ticket creation date and the first answer by a staff user
        :rtype: datetime.timedelta|None
        """
        first_answer = None
        # Use followup_set.all(), it should have been prefetch to be optimized
        for followup in reversed(self.followup_set.all()):  # Reverse it since it is DESC by default and we want ASC
            if followup.public and followup.user and followup.user.is_staff:
                first_answer = followup
                break
        if first_answer:
            # If a ticket has a followup older than its creation (because of a fusion), return 0
            if self.created > first_answer.date:
                return datetime.timedelta(0)
            return office_time_between(self.created, first_answer.date)
        return None

    def send_notification_changed_assigned_to(self, user):
        """
        Send a notification to the new ticket's owner

        :param User user: user that made the change of assigned_to
        """
        if self.assigned_to:
            Notification.objects.create(
                user_list=[self.assigned_to],
                module=Notification.TICKET,
                message=f"{user} vient de t'affecter le ticket {self}.",
                link_redirect=self.get_absolute_url()
            )


class FollowUpManager(models.Manager):

    def private_followups(self):
        return self.filter(public=False)

    def public_followups(self):
        return self.filter(public=True)


class FollowUp(models.Model):
    """
    A FollowUp is a comment and/or change to a ticket. We keep a simple
    title, the comment entered by the user, and the new status of a ticket
    to enable easy flagging of details on the view-ticket page.

    The title is automatically generated at save-time, based on what action
    the user took.

    Tickets that aren't public are never shown to or e-mailed to the submitter,
    although all staff can see them.
    """

    ticket = models.ForeignKey(
        Ticket,
        on_delete=models.CASCADE,
        verbose_name=_('Ticket'),
    )

    date = models.DateTimeField(
        _('Date'),
        default=timezone.now
    )

    title = models.CharField(
        _('Title'),
        max_length=200,
        blank=True,
        null=True,
    )

    comment = HTMLField(
        _('Comment'),
        blank=True
    )

    public = models.BooleanField(
        _('Public'),
        blank=True,
        default=False,
        help_text=_('Public tickets are viewable by the submitter and all '
                    'staff, but non-public tickets can only be seen by staff.'),
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        verbose_name=_('User'),
    )

    new_status = models.IntegerField(
        _('New Status'),
        choices=Ticket.STATUS_CHOICES,
        blank=True,
        null=True,
        help_text=_('If the status was changed, what was it changed to?'),
    )

    objects = FollowUpManager()

    class Meta:
        ordering = ('-date',)
        verbose_name = _('Follow-up')
        verbose_name_plural = _('Follow-ups')

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return f'{self.ticket.get_absolute_url()}#followup{self.id}'

    def save(self, *args, **kwargs):
        # Cascade update ticket's modified date field
        self.ticket.modified = timezone.now()

        # Check if this is the first public answer by a staff user on the ticket
        if not self.ticket.time_before_first_answer and self.public and self.user and self.user.is_staff:
            # Save the difference between ticket's creation date and this followup's date
            self.ticket.time_before_first_answer = office_time_between(self.ticket.created, self.date)

        self.ticket.save()
        super(FollowUp, self).save(*args, **kwargs)

    def append_signature(self):
        """ Append the signature at the bottom of the followup message if user is defined """
        if not self.user:
            return
        # Define default signature
        signature = f'<p><strong>{self.user}</strong></p>'
        # Override signature if employee user has one
        if self.user.employee.signature:
            signature = self.user.employee.signature
        # Append signature preceded by a newline in order to conserve it later in plain text version of the sent mails
        self.comment += f"""<br>
        {signature}"""


class TicketChange(models.Model):
    """
    For each FollowUp, any changes to the parent ticket (eg Title, Priority,
    etc) are tracked here for display purposes.
    """

    followup = models.ForeignKey(
        FollowUp,
        on_delete=models.CASCADE,
        verbose_name=_('Follow-up'),
    )

    field = models.CharField(
        _('Field'),
        max_length=100,
    )

    old_value = models.TextField(
        _('Old Value'),
        blank=True,
        null=True,
    )

    new_value = models.TextField(
        _('New Value'),
        blank=True,
        null=True,
    )

    def __str__(self):
        out = '%s ' % self.field
        if not self.new_value:
            out += ugettext('removed')
        elif not self.old_value:
            out += ugettext('set to %s') % self.new_value
        else:
            out += ugettext('changed from "%(old_value)s" to "%(new_value)s"') % {
                'old_value': self.old_value,
                'new_value': self.new_value
            }
        return out

    class Meta:
        verbose_name = _('Ticket change')
        verbose_name_plural = _('Ticket changes')


class FeedbackSurveyQueryset(models.QuerySet):
    def bad(self):
        return self.filter(score=0)

    def neutral(self):
        return self.filter(score=1)

    def good(self):
        return self.filter(score=2)


class FeedbackSurvey(models.Model):
    ticket = models.ForeignKey(
        Ticket,
        verbose_name=_('Ticket'),
        on_delete=models.CASCADE,
        related_name='feedback_surveys'
    )
    created_at = models.DateTimeField(
        _('Created'),
        auto_now_add=True
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_('Author'),
        on_delete=models.SET_NULL,
        related_name='feedback_survey_answers',
        null=True,
        blank=True
    )
    score = models.PositiveSmallIntegerField(
        _('Score'),
        validators=[MaxValueValidator(2)]
    )
    message = models.TextField(
        _('Message'),
        blank=True
    )

    objects = FeedbackSurveyQueryset.as_manager()


def attachment_path(instance, filename):
    """
    Provide a file path that will help prevent files being overwritten, by
    putting attachments in a folder off attachments for ticket/followup_id/.
    """
    import os
    os.umask(0)
    path = 'helpdesk/attachments/%s/%s' % (instance.followup.ticket.ticket_for_url, instance.followup.id)
    att_path = os.path.join(settings.MEDIA_ROOT, path)
    if settings.DEFAULT_FILE_STORAGE == "django.core.files.storage.FileSystemStorage":
        if not os.path.exists(att_path):
            # TODO: is there a better way to handle directory permissions more consistently?
            os.makedirs(att_path, 0o755)
    return os.path.join(path, filename)


class Attachment(models.Model):
    """
    Represents a file attached to a follow-up. This could come from an e-mail
    attachment, or it could be uploaded via the web interface.
    """

    followup = models.ForeignKey(
        FollowUp,
        on_delete=models.CASCADE,
        verbose_name=_('Follow-up'),
    )

    file = models.FileField(
        _('File'),
        upload_to=attachment_path,
        max_length=1000,
    )

    filename = models.CharField(
        _('Filename'),
        max_length=1000,
    )

    mime_type = models.CharField(
        _('MIME Type'),
        max_length=255,
    )

    size = models.IntegerField(
        _('Size'),
        help_text=_('Size of this file in bytes'),
    )

    def __str__(self):
        return '%s' % self.filename

    class Meta:
        ordering = ('filename',)
        verbose_name = _('Attachment')
        verbose_name_plural = _('Attachments')


class PreSetReply(models.Model):
    """
    We can allow the admin to define a number of pre-set replies, used to
    simplify the sending of updates and resolutions. These are basically Django
    templates with a limited context - however if you wanted to get crafy it would
    be easy to write a reply that displays ALL updates in hierarchical order etc
    with use of for loops over {{ ticket.followup_set.all }} and friends.

    When replying to a ticket, the user can select any reply set for the current
    queue, and the body text is fetched via AJAX.
    """
    class Meta:
        ordering = ('name',)
        verbose_name = _('Pre-set reply')
        verbose_name_plural = _('Pre-set replies')

    queues = models.ManyToManyField(
        Queue,
        blank=True,
        help_text=_('Leave blank to allow this reply to be used for all '
                    'queues, or select those queues you wish to limit this reply to.'),
    )

    name = models.CharField(
        _('Name'),
        max_length=100,
        help_text=_('Only used to assist users with selecting a reply - not '
                    'shown to the user.'),
    )

    body = HTMLField(
        _('Body'),
        help_text=_('Context available: {{ ticket }} - ticket object (eg '
                    '{{ ticket.title }}); {{ queue }} - The queue; and {{ user }} '
                    '- the current user.'),
    )

    def __str__(self):
        return '%s' % self.name


class EscalationExclusion(models.Model):
    """
    An 'EscalationExclusion' lets us define a date on which escalation should
    not happen, for example a weekend or public holiday.

    You may also have a queue that is only used on one day per week.

    To create these on a regular basis, check out the README file for an
    example cronjob that runs 'create_escalation_exclusions.py'.
    """

    queues = models.ManyToManyField(
        Queue,
        blank=True,
        help_text=_('Leave blank for this exclusion to be applied to all queues, '
                    'or select those queues you wish to exclude with this entry.'),
    )

    name = models.CharField(
        _('Name'),
        max_length=100,
    )

    date = models.DateField(
        _('Date'),
        help_text=_('Date on which escalation should not happen'),
    )

    def __str__(self):
        return '%s' % self.name

    class Meta:
        verbose_name = _('Escalation exclusion')
        verbose_name_plural = _('Escalation exclusions')


class EmailTemplate(models.Model):
    """
    Since these are more likely to be changed than other templates, we store
    them in the database.

    This means that an admin can change email templates without having to have
    access to the filesystem.
    """

    template_name = models.CharField(
        _('Template Name'),
        max_length=100,
    )

    subject = models.CharField(
        _('Subject'),
        max_length=100,
        help_text=_('This will be prefixed with "[ticket.ticket] ticket.title"'
                    '. We recommend something simple such as "(Updated") or "(Closed)"'
                    ' - the same context is available as in plain_text, below.'),
    )

    heading = models.CharField(
        _('Heading'),
        max_length=100,
        help_text=_('In HTML e-mails, this will be the heading at the top of '
                    'the email - the same context is available as in plain_text, '
                    'below.'),
    )

    plain_text = models.TextField(
        _('Plain Text'),
        help_text=_('The context available to you includes {{ ticket }}, '
                    '{{ queue }}, and depending on the time of the call: '
                    '{{ resolution }} or {{ comment }}.'),
    )

    html = models.TextField(
        _('HTML'),
        help_text=_('The same context is available here as in plain_text, above.'),
    )

    locale = models.CharField(
        _('Locale'),
        max_length=10,
        blank=True,
        null=True,
        help_text=_('Locale of this template.'),
    )

    def __str__(self):
        return '%s' % self.template_name

    class Meta:
        ordering = ('template_name', 'locale')
        verbose_name = _('e-mail template')
        verbose_name_plural = _('e-mail templates')


class KBCategory(models.Model):
    """
    Lets help users help themselves: the Knowledge Base is a categorised
    listing of questions & answers.
    """

    title = models.CharField(
        _('Title'),
        max_length=100,
    )

    slug = models.SlugField(
        _('Slug'),
    )

    description = models.TextField(
        _('Description'),
    )

    def __str__(self):
        return '%s' % self.title

    class Meta:
        ordering = ('title',)
        verbose_name = _('Knowledge base category')
        verbose_name_plural = _('Knowledge base categories')

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse('helpdesk:kb_category', kwargs={'slug': self.slug})


class KBItem(models.Model):
    """
    An item within the knowledgebase. Very straightforward question/answer
    style system.
    """
    category = models.ForeignKey(
        KBCategory,
        on_delete=models.CASCADE,
        verbose_name=_('Category'),
    )

    title = models.CharField(
        _('Title'),
        max_length=100,
    )

    question = models.TextField(
        _('Question'),
    )

    answer = models.TextField(
        _('Answer'),
    )

    votes = models.IntegerField(
        _('Votes'),
        help_text=_('Total number of votes cast for this item'),
        default=0,
    )

    recommendations = models.IntegerField(
        _('Positive Votes'),
        help_text=_('Number of votes for this item which were POSITIVE.'),
        default=0,
    )

    last_updated = models.DateTimeField(
        _('Last Updated'),
        help_text=_('The date on which this question was most recently changed.'),
        blank=True,
    )

    def save(self, *args, **kwargs):
        if not self.last_updated:
            self.last_updated = timezone.now()
        return super(KBItem, self).save(*args, **kwargs)

    def _score(self):
        """ Return a score out of 10 or Unrated if no votes """
        if self.votes > 0:
            return (self.recommendations / self.votes) * 10
        else:
            return _('Unrated')
    score = property(_score)

    def __str__(self):
        return '%s' % self.title

    class Meta:
        ordering = ('title',)
        verbose_name = _('Knowledge base item')
        verbose_name_plural = _('Knowledge base items')

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse('helpdesk:kb_item', args=(self.id,))


class SavedSearch(models.Model):
    """
    Allow a user to save a ticket search, eg their filtering and sorting
    options, and optionally share it with other users. This lets people
    easily create a set of commonly-used filters, such as:
        * My tickets waiting on me
        * My tickets waiting on submitter
        * My tickets in 'Priority Support' queue with priority of 1
        * All tickets containing the word 'billing'.
         etc...
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        verbose_name=_('User'),
    )

    title = models.CharField(
        _('Query Name'),
        max_length=100,
        help_text=_('User-provided name for this query'),
    )

    shared = models.BooleanField(
        _('Shared With Other Users?'),
        blank=True,
        default=False,
        help_text=_('Should other users see this query?'),
    )

    query = models.TextField(
        _('Search Query'),
        help_text=_('Pickled query object. Be wary changing this.'),
    )

    def __str__(self):
        if self.shared:
            return '%s (*)' % self.title
        else:
            return '%s' % self.title

    class Meta:
        verbose_name = _('Saved search')
        verbose_name_plural = _('Saved searches')

    def get_absolute_url(self):
        return '%s?saved_query=%s' % (reverse('helpdesk:list'), self.id)


class UserSettings(models.Model):
    """
    A bunch of user-specific settings that we want to be able to define, such
    as notification preferences and other things that should probably be
    configurable.

    We should always refer to user.usersettings_helpdesk.settings['setting_name'].
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="usersettings_helpdesk")

    settings_pickled = models.TextField(
        _('Settings Dictionary'),
        help_text=_('This is a base64-encoded representation of a pickled Python dictionary. '
                    'Do not change this field via the admin.'),
        blank=True,
        null=True,
    )

    def _set_settings(self, data):
        # data should always be a Python dictionary.
        try:
            import pickle
        except ImportError:
            import cPickle as pickle
        from helpdesk.lib import b64encode
        self.settings_pickled = b64encode(pickle.dumps(data)).decode()

    def _get_settings(self):
        # return a python dictionary representing the pickled data.
        try:
            import pickle
        except ImportError:
            import cPickle as pickle
        from helpdesk.lib import b64decode
        try:
            return pickle.loads(b64decode(self.settings_pickled.encode('utf-8')))
        except pickle.UnpicklingError:
            return {}

    settings = property(_get_settings, _set_settings)

    def __str__(self):
        return 'Preferences for %s' % self.user

    class Meta:
        verbose_name = _('User Setting')
        verbose_name_plural = _('User Settings')


def create_usersettings(sender, instance, created, **kwargs):
    """
    Helper function to create UserSettings instances as
    required, eg when we first create the UserSettings database
    table via 'syncdb' or when we save a new user.

    If we end up with users with no UserSettings, then we get horrible
    'DoesNotExist: UserSettings matching query does not exist.' errors.
    """
    from helpdesk.settings import DEFAULT_USER_SETTINGS
    if created:
        UserSettings.objects.create(user=instance, settings=DEFAULT_USER_SETTINGS)


models.signals.post_save.connect(create_usersettings, sender=settings.AUTH_USER_MODEL)


class IgnoreEmail(models.Model):
    """
    This model lets us easily ignore e-mails from certain senders when
    processing IMAP and POP3 mailboxes, eg mails from postmaster or from
    known trouble-makers.
    """
    class Meta:
        verbose_name = _('Ignored e-mail address')
        verbose_name_plural = _('Ignored e-mail addresses')

    queues = models.ManyToManyField(
        Queue,
        blank=True,
        help_text=_('Leave blank for this e-mail to be ignored on all queues, '
                    'or select those queues you wish to ignore this e-mail for.'),
    )

    name = models.CharField(
        _('Name'),
        max_length=100,
    )

    date = models.DateField(
        _('Date'),
        help_text=_('Date on which this e-mail address was added'),
        blank=True,
        editable=False
    )

    email_address = models.CharField(
        _('E-Mail Address'),
        max_length=150,
        help_text=_('Enter a full e-mail address, or portions with '
                    'wildcards, eg *@domain.com or postmaster@*.'),
    )

    keep_in_mailbox = models.BooleanField(
        _('Save Emails in Mailbox?'),
        blank=True,
        default=False,
        help_text=_('Do you want to save emails from this address in the mailbox? '
                    'If this is unticked, emails from this address will be deleted.'),
    )

    def __str__(self):
        return '%s' % self.name

    def save(self, *args, **kwargs):
        if not self.date:
            self.date = timezone.now()
        return super(IgnoreEmail, self).save(*args, **kwargs)

    def queue_list(self):
        """Return a list of the queues this IgnoreEmail applies to.
        If this IgnoreEmail applies to ALL queues, return '*'.
        """
        queues = self.queues.all().order_by('title')
        if len(queues) == 0:
            return '*'
        else:
            return ', '.join([str(q) for q in queues])

    def test(self, email):
        """
        Possible situations:
            1. Username & Domain both match
            2. Username is wildcard, domain matches
            3. Username matches, domain is wildcard
            4. username & domain are both wildcards
            5. Other (no match)

            1-4 return True, 5 returns False.
        """

        own_parts = self.email_address.split("@")
        email_parts = email.split("@")

        if self.email_address == email or \
                own_parts[0] == "*" and own_parts[1] == email_parts[1] or \
                own_parts[1] == "*" and own_parts[0] == email_parts[0] or \
                own_parts[0] == "*" and own_parts[1] == "*":
            return True
        else:
            return False


class TicketCC(models.Model):
    """
    Often, there are people who wish to follow a ticket who aren't the
    person who originally submitted it. This model provides a way for those
    people to follow a ticket.

    In this circumstance, a 'person' could be either an e-mail address or
    an existing system user.
    """

    ticket = models.ForeignKey(
        Ticket,
        on_delete=models.CASCADE,
        verbose_name=_('Ticket'),
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        help_text=_('User who wishes to receive updates for this ticket.'),
        verbose_name=_('User'),
    )

    email = models.EmailField(
        _('E-Mail Address'),
        blank=True,
        null=True,
        help_text=_('For non-user followers, enter their e-mail address'),
    )

    can_view = models.BooleanField(
        _('Can View Ticket?'),
        blank=True,
        default=False,
        help_text=_('Can this CC login to view the ticket details?'),
    )

    can_update = models.BooleanField(
        _('Can Update Ticket?'),
        blank=True,
        default=False,
        help_text=_('Can this CC login and update the ticket?'),
    )

    def _email_address(self):
        if self.user and self.user.email:
            return self.user.email
        else:
            return self.email
    email_address = property(_email_address)

    def _display(self):
        if self.user:
            return '{} <{}>'.format(self.user, self.user.email)
        else:
            return self.email
    display = property(_display)

    def __str__(self):
        return f'{self.display} pour {self.ticket.title}'


class CustomFieldManager(models.Manager):

    def get_queryset(self):
        return super(CustomFieldManager, self).get_queryset().order_by('ordering')


class CustomField(models.Model):
    """
    Definitions for custom fields that are glued onto each ticket.
    """

    name = models.SlugField(
        _('Field Name'),
        help_text=_('As used in the database and behind the scenes. '
                    'Must be unique and consist of only lowercase letters with no punctuation.'),
        unique=True,
    )

    label = models.CharField(
        _('Label'),
        max_length=30,
        help_text=_('The display label for this field'),
    )

    help_text = models.TextField(
        _('Help Text'),
        help_text=_('Shown to the user when editing the ticket'),
        blank=True,
        null=True
    )

    DATA_TYPE_CHOICES = (
        ('varchar', _('Character (single line)')),
        ('text', _('Text (multi-line)')),
        ('integer', _('Integer')),
        ('decimal', _('Decimal')),
        ('list', _('List')),
        ('boolean', _('Boolean (checkbox yes/no)')),
        ('date', _('Date')),
        ('time', _('Time')),
        ('datetime', _('Date & Time')),
        ('email', _('E-Mail Address')),
        ('url', _('URL')),
        ('ipaddress', _('IP Address')),
        ('slug', _('Slug')),
    )

    data_type = models.CharField(
        _('Data Type'),
        max_length=100,
        help_text=_('Allows you to restrict the data entered into this field'),
        choices=DATA_TYPE_CHOICES,
    )

    max_length = models.IntegerField(
        _('Maximum Length (characters)'),
        blank=True,
        null=True,
    )

    decimal_places = models.IntegerField(
        _('Decimal Places'),
        help_text=_('Only used for decimal fields'),
        blank=True,
        null=True,
    )

    empty_selection_list = models.BooleanField(
        _('Add empty first choice to List?'),
        default=False,
        help_text=_('Only for List: adds an empty first entry to the choices list, '
                    'which enforces that the user makes an active choice.'),
    )

    list_values = models.TextField(
        _('List Values'),
        help_text=_('For list fields only. Enter one option per line.'),
        blank=True,
        null=True,
    )

    ordering = models.IntegerField(
        _('Ordering'),
        help_text=_('Lower numbers are displayed first; higher numbers are listed later'),
        blank=True,
        null=True,
    )

    def _choices_as_array(self):
        valuebuffer = StringIO(self.list_values)
        choices = [[item.strip(), item.strip()] for item in valuebuffer.readlines()]
        valuebuffer.close()
        return choices
    choices_as_array = property(_choices_as_array)

    required = models.BooleanField(
        _('Required?'),
        help_text=_('Does the user have to enter a value for this field?'),
        default=False,
    )

    staff_only = models.BooleanField(
        _('Staff Only?'),
        help_text=_('If this is ticked, then the public submission form '
                    'will NOT show this field'),
        default=False,
    )

    objects = CustomFieldManager()

    def __str__(self):
        return '%s' % self.name

    class Meta:
        verbose_name = _('Custom field')
        verbose_name_plural = _('Custom fields')


class TicketCustomFieldValue(models.Model):
    ticket = models.ForeignKey(
        Ticket,
        on_delete=models.CASCADE,
        verbose_name=_('Ticket'),
    )

    field = models.ForeignKey(
        CustomField,
        on_delete=models.CASCADE,
        verbose_name=_('Field'),
    )

    value = models.TextField(blank=True, null=True)

    def __str__(self):
        return '%s / %s' % (self.ticket, self.field)

    class Meta:
        unique_together = (('ticket', 'field'),)
        verbose_name = _('Ticket custom field value')
        verbose_name_plural = _('Ticket custom field values')


class TicketDependency(models.Model):
    """
    The ticket identified by `ticket` cannot be resolved until the ticket in `depends_on` has been resolved.
    To help enforce this, a helper function `can_be_resolved` on each Ticket instance checks that
    these have all been resolved.
    """
    class Meta:
        unique_together = (('ticket', 'depends_on'),)
        verbose_name = _('Ticket dependency')
        verbose_name_plural = _('Ticket dependencies')

    ticket = models.ForeignKey(
        Ticket,
        on_delete=models.CASCADE,
        verbose_name=_('Ticket'),
        related_name='ticketdependency',
    )

    depends_on = models.ForeignKey(
        Ticket,
        on_delete=models.CASCADE,
        verbose_name=_('Depends On Ticket'),
        related_name='depends_on',
    )

    def __str__(self):
        return '%s / %s' % (self.ticket, self.depends_on)
