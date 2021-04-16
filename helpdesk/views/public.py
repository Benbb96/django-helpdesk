"""
django-helpdesk - A Django powered ticket tracker for small enterprise.

(c) Copyright 2008 Jutda. All Rights Reserved. See LICENSE for details.

views/public.py - All public facing views, eg non-staff (no authentication
                  required) views.
"""
from django.db.models import Q

from base.models import Notification

from django.urls import reverse
from django.http import HttpResponseRedirect, HttpResponse
from django.shortcuts import render, get_object_or_404
from django.utils.http import urlquote
from django.utils.translation import ugettext as _
from django.conf import settings

from helpdesk import settings as helpdesk_settings
from helpdesk.decorators import protect_view
from helpdesk.forms import PublicTicketForm, FeedbackSurveyForm
from helpdesk.lib import text_is_spam
from helpdesk.models import Ticket, Queue, UserSettings, KBCategory


@protect_view
def homepage(request):
    if request.user.is_staff or \
            (request.user.is_authenticated and
             helpdesk_settings.HELPDESK_ALLOW_NON_STAFF_TICKET_UPDATE):
        try:
            if request.user.usersettings_helpdesk.settings.get('login_view_ticketlist', False):
                return HttpResponseRedirect(reverse('helpdesk:list'))
            else:
                return HttpResponseRedirect(reverse('helpdesk:dashboard'))
        except UserSettings.DoesNotExist:
            return HttpResponseRedirect(reverse('helpdesk:dashboard'))

    if request.method == 'POST':
        form = PublicTicketForm(request.POST, request.FILES)
        form.fields['queue'].choices = [('', '--------')] + [
            (q.id, q.title) for q in Queue.objects.filter(allow_public_submission=True)]
        if form.is_valid():
            if text_is_spam(form.cleaned_data['body'], request):
                # This submission is spam. Let's not save it.
                return render(request, template_name='helpdesk/public_spam.html')
            else:
                ticket = form.save(
                    user=request.user if request.user.is_authenticated else None)
                try:
                    return HttpResponseRedirect('%s?ticket=%s&email=%s' % (
                        reverse('helpdesk:public_view'),
                        ticket.ticket_for_url,
                        urlquote(ticket.submitter_email))
                    )
                except ValueError:
                    # if someone enters a non-int string for the ticket
                    return HttpResponseRedirect(reverse('helpdesk:home'))
    else:
        try:
            queue = Queue.objects.get(slug=request.GET.get('queue', None))
        except Queue.DoesNotExist:
            queue = None
        initial_data = {}

        # add pre-defined data for public ticket
        if hasattr(settings, 'HELPDESK_PUBLIC_TICKET_QUEUE'):
            # get the requested queue; return an error if queue not found
            try:
                queue = Queue.objects.get(slug=settings.HELPDESK_PUBLIC_TICKET_QUEUE)
            except Queue.DoesNotExist:
                return HttpResponse(status=500)
        if hasattr(settings, 'HELPDESK_PUBLIC_TICKET_PRIORITY'):
            initial_data['priority'] = settings.HELPDESK_PUBLIC_TICKET_PRIORITY
        if hasattr(settings, 'HELPDESK_PUBLIC_TICKET_DUE_DATE'):
            initial_data['due_date'] = settings.HELPDESK_PUBLIC_TICKET_DUE_DATE

        if queue:
            initial_data['queue'] = queue.id

        if request.user.is_authenticated and request.user.email:
            initial_data['submitter_email'] = request.user.email

        form = PublicTicketForm(initial=initial_data)

    knowledgebase_categories = None

    if helpdesk_settings.HELPDESK_KB_ENABLED:
        knowledgebase_categories = KBCategory.objects.all()

    return render(request, 'helpdesk/public_homepage.html', {
        'form': form,
        'helpdesk_settings': helpdesk_settings,
        'kb_categories': knowledgebase_categories
    })


@protect_view
def view_ticket(request):
    ticket_req = request.GET.get('ticket', None)
    email = request.GET.get('email', None)

    if ticket_req and email:
        queue, ticket_id = Ticket.queue_and_id_from_query(ticket_req)
        try:
            ticket = Ticket.objects.filter(
                Q(id=ticket_id) & (
                    Q(submitter_email__iexact=email) | (Q(ticketcc__email__iexact=email) & Q(ticketcc__can_view=True))
                )
            ).distinct()
            if len(ticket) == 0:
                raise ValueError
            elif len(ticket) > 1:
                print('Plusieurs tickets correspondent :')
                print(ticket)
                raise ValueError
            else:
                ticket = ticket.get()
        except ValueError:
            error_message = _('Invalid ticket ID or e-mail address. Please try again.')
        else:
            if request.user.is_staff:
                redirect_url = reverse('helpdesk:view', args=[ticket_id])
                if 'close' in request.GET:
                    redirect_url += '?close'
                return HttpResponseRedirect(redirect_url)

            if 'close' in request.GET and ticket.status == Ticket.RESOLVED_STATUS:
                from helpdesk.views.staff import update_ticket
                # Trick the update_ticket() view into thinking it's being called with
                # a valid POST.
                request.POST = {
                    'new_status': Ticket.CLOSED_STATUS,
                    'public': 1,
                    'title': ticket.title,
                    'comment': _('Submitter accepted resolution and closed ticket'),
                }
                if ticket.assigned_to:
                    request.POST['owner'] = ticket.assigned_to.id
                request.GET = {}

                return update_ticket(request, ticket_id, public=True)

            # redirect user back to this ticket if possible.
            redirect_url = ''
            if helpdesk_settings.HELPDESK_NAVIGATION_ENABLED:
                redirect_url = reverse('helpdesk:view', args=[ticket_id])

            return render(request, 'helpdesk/public_view_ticket.html', {
                'ticket': ticket,
                'helpdesk_settings': helpdesk_settings,
                'next': redirect_url,
            })
    elif ticket_req is None and email is None:
        error_message = None
    else:
        error_message = _('Missing ticket ID or e-mail address. Please try again.')

    return render(request, 'helpdesk/public_view_form.html', {
        'ticket': ticket_req,
        'email': email,
        'error_message': error_message,
        'helpdesk_settings': helpdesk_settings,
    })


def change_language(request):
    return_to = ''
    if 'return_to' in request.GET:
        return_to = request.GET['return_to']

    return render(request, 'helpdesk/public_change_language.html', {'next': return_to})


def feedback_survey_view(request, ticket_id):
    ticket = get_object_or_404(Ticket, id=ticket_id)
    form = FeedbackSurveyForm(request.POST or None, initial={'score': request.GET.get('note')})
    success = False
    if form.is_valid():
        feedback_survey = form.save(commit=False)
        feedback_survey.ticket = ticket
        if request.user.is_authenticated:
            feedback_survey.author = request.user
        feedback_survey.save()
        link_redirect = '%s?tickets=%d' % (reverse('helpdesk:feedback_survey_list'), ticket.id)
        if ticket.assigned_to:
            Notification.objects.create(
                message="Tu viens de recevoir un nouveau feedback sur ton ticket %s" % ticket,
                module=Notification.TICKET,
                link_redirect=link_redirect,
                user_list=[ticket.assigned_to]
            )
        else:
            Notification.objects.create_for_technical_service(
                message="Nouvelle réponse à une enquête de satisfaction sur le ticket %s" % ticket,
                module=Notification.TICKET,
                link_redirect=link_redirect
            )
        success = True
    return render(request, 'helpdesk/public_feedback_survey.html', {
        'ticket': ticket,
        'form': form,
        'success': success
    })
