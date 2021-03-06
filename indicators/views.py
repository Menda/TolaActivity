import json
import re

from django.db import connection
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.http import HttpResponseRedirect
from urlparse import urlparse
from django.shortcuts import render_to_response
from django.contrib import messages
from django.core.serializers.json import DjangoJSONEncoder

from django.db.models import Count, Sum
from django.db.models import Q
from django.contrib.auth.decorators import user_passes_test
from django.core.exceptions import PermissionDenied
from django.core import serializers
from django.http import HttpResponse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.generic import TemplateView
from django.views.generic.edit import CreateView, UpdateView, DeleteView
from django.views.generic.list import ListView
from django.views.generic.detail import View
import requests

from export import IndicatorResource, CollectedDataResource
from .models import Indicator, PeriodicTarget, DisaggregationLabel, DisaggregationValue, CollectedData, IndicatorType, Level, ExternalServiceRecord, ExternalService, TolaTable
from workflow.models import WorkflowLevel1, SiteProfile, Country, Sector, TolaSites, TolaUser, FormGuidance
from tola.util import getCountry, get_table
from .forms import IndicatorForm, CollectedDataForm


def group_excluded(*group_names, **url):
    """
    If user is in the group passed in permission denied
    :param group_names:
    :param url:
    :return: Bool True or False is users passes test
    """
    def in_groups(u):
        if u.is_authenticated():
            if not bool(u.groups.filter(name__in=group_names)):
                return True
            raise PermissionDenied
        return False
    return user_passes_test(in_groups)


class IndicatorList(ListView):
    """
    Main Indicator Home Page, displays a list of Indicators Filterable by Program
    """
    model = Indicator
    template_name = 'indicators/indicator_list.html'

    def get(self, request, *args, **kwargs):

        countries = getCountry(request.user)
        getPrograms = WorkflowLevel1.objects.all().filter(country__in=countries).distinct()
        getIndicators = Indicator.objects.all().filter(workflowlevel1__country__in=countries).exclude(collecteddata__isnull=True).order_by("-number")
        getIndicatorTypes = IndicatorType.objects.all()
        workflowlevel1 = self.kwargs['workflowlevel1']
        indicator = self.kwargs['indicator']
        type = self.kwargs['type']
        indicator_name = ""
        type_name = ""
        workflowlevel1_name = ""

        q = {'id__isnull': False}
        # if we have a workflowlevel1 filter active
        if int(workflowlevel1) != 0:
            q = {
                'id': workflowlevel1,
            }
            # redress the indicator list based on workflowlevel1
            getIndicators = Indicator.objects.select_related().filter(workflowlevel1=workflowlevel1)
            workflowlevel1_name = WorkflowLevel1.objects.get(id=workflowlevel1)
        # if we have an indicator type active
        if int(type) != 0:
            r = {
                'indicator__indicator_type__id': type,
            }
            q.update(r)
            # redress the indicator list based on type
            getIndicators = Indicator.objects.select_related().filter(indicator_type__id=type)
            type_name = IndicatorType.objects.get(id=type).indicator_type
        # if we have an indicator id append it to the query filter
        if int(indicator) != 0:
            s = {
                'indicator': indicator,
            }
            q.update(s)
            indicator_name = Indicator.objects.get(id=indicator)

        indicators = WorkflowLevel1.objects.all().filter(country__in=countries).filter(**q).order_by('name').annotate(indicator_count=Count('indicator'))
        return render(request, self.template_name, {'getPrograms': getPrograms,'getIndicators':getIndicators,
                                                    'workflowlevel1_name':workflowlevel1_name, 'indicator_name':indicator_name,
                                                    'type_name':type_name, 'workflowlevel1':workflowlevel1, 'indicator': indicator, 'type': type,
                                                    'getProgramsIndicator': indicators, 'getIndicatorTypes': getIndicatorTypes})


def import_indicator(service=1,deserialize=True):
    """
    Import a indicators from a web service (the dig only for now)
    :param service:
    :param deserialize:
    :return:
    """
    service = ExternalService.objects.get(id=service)
    response = requests.get(service.feed_url)

    if deserialize == True:
        data = json.loads(response.content) # deserialises it
    else:
        # send json data back not deserialized data
        data = response
    #debug the json data string uncomment dump and print
    #data2 = json.dumps(json_data) # json formatted string
    #print data2

    return data


def indicator_create(request, id=0):
    """
    Create an Indicator with a service template first, or custom.  Step one in Inidcator creation.
    Passed on to IndicatorCreate to do the creation
    :param request:
    :param id:
    :return:
    """
    getIndicatorTypes = IndicatorType.objects.all()
    getCountries = Country.objects.all()
    countries = getCountry(request.user)
    country_id = Country.objects.get(country=countries[0]).id
    getPrograms = WorkflowLevel1.objects.all().filter( country__in=countries).distinct()
    getServices = ExternalService.objects.all()
    workflowlevel1_id = id

    if request.method == 'POST':
        #set vars from form and get values from user

        type = IndicatorType.objects.get(indicator_type="Custom")
        country = Country.objects.get(id=request.POST['country'])
        workflowlevel1 = WorkflowLevel1.objects.get(id=request.POST['workflowlevel1'])
        if 'services' in request.POST:
            service = request.POST['services']
        else:
            service = None

        if 'service_indicator' in request.POST:
            node_id = request.POST['service_indicator']
        else:
            node_id = None
        level = Level.objects.all()[0]
        sector = None
        # add a temp name for custom indicators
        name = "Temporary"
        source = None
        definition = None
        external_service_record = None

        #import recursive library for substitution
        import re

        #checkfor service indicator and update based on values
        if node_id != None and int(node_id) != 0:
            getImportedIndicators = import_indicator(service)
            for item in getImportedIndicators:
                if item['nid'] == node_id:
                    getSector, created = Sector.objects.get_or_create(sector=item['sector'])
                    sector=getSector
                    getLevel, created = Level.objects.get_or_create(name=item['level'].title())
                    level=getLevel
                    name=item['title']
                    source=item['source']
                    definition=item['definition']
                    #replace HTML tags if they are in the string
                    definition = re.sub("<.*?>", "", definition)

                    getService = ExternalService.objects.get(id=service)
                    full_url = getService.url + "/" + item['nid']
                    external_service_record = ExternalServiceRecord(record_id=item['nid'],external_service=getService,full_url=full_url)
                    external_service_record.save()
                    getType, created = IndicatorType.objects.get_or_create(indicator_type=item['type'].title())
                    type=getType

        #save form
        new_indicator = Indicator(sector=sector,name=name,source=source,definition=definition, external_service_record=external_service_record)
        new_indicator.save()
        new_indicator.workflowlevel1.add(workflowlevel1)
        new_indicator.indicator_type.add(type)
        new_indicator.level = level

        latest = new_indicator.id

        #redirect to update page
        messages.success(request, 'Success, Basic Indicator Created!')
        redirect_url = '/indicators/indicator_update/' + str(latest)+ '/'
        return HttpResponseRedirect(redirect_url)

    # send the keys and vars from the json data to the template along with submitted feed info and silos for new form
    return render(request, "indicators/indicator_create.html", {'country_id': country_id, 'workflowlevel1_id':int(workflowlevel1_id),'getCountries':getCountries,
                                                                'getPrograms': getPrograms,'getIndicatorTypes':getIndicatorTypes, 'getServices': getServices})


class IndicatorCreate(CreateView):
    """
    Indicator Form for indicators not using a template or service indicator first as well as the post reciever
    for creating an indicator.  Then redirect back to edit view in IndicatorUpdate.
    """
    model = Indicator
    template_name = 'indicators/indicator_form.html'

    #pre-populate parts of the form
    def get_initial(self):
        user_profile = TolaUser.objects.get(user=self.request.user)
        initial = {
            'workflowlevel1': self.kwargs['id'],
            }

        return initial

    def get_context_data(self, **kwargs):
        context = super(IndicatorCreate, self).get_context_data(**kwargs)
        context.update({'id': self.kwargs['id']})
        return context

    @method_decorator(group_excluded('ViewOnly', url='workflow/permission'))
    def dispatch(self, request, *args, **kwargs):
        return super(IndicatorCreate, self).dispatch(request, *args, **kwargs)

    # add the request to the kwargs
    def get_form_kwargs(self):
        kwargs = super(IndicatorCreate, self).get_form_kwargs()
        kwargs['request'] = self.request
        workflowlevel1 = Indicator.objects.all().filter(id=self.kwargs['pk']).values("workflowlevel1__id")
        kwargs['workflowlevel1'] = workflowlevel1
        return kwargs

    def form_invalid(self, form):

        messages.error(self.request, 'Invalid Form', fail_silently=False)

        return self.render_to_response(self.get_context_data(form=form))

    def form_valid(self, form):
        form.save()
        messages.success(self.request, 'Success, Indicator Created!')
        form = ""
        return self.render_to_response(self.get_context_data(form=form))

    form_class = IndicatorForm


class IndicatorUpdate(UpdateView):
    """
    Update and Edit Indicators.
    """
    model = Indicator
    #template_name = 'indicators/indicator_form.html'
    def get_template_names(self):
        if self.request.GET.get('modal'):
            return 'indicators/indicator_form_modal.html'
        return 'indicators/indicator_form.html'

    @method_decorator(group_excluded('ViewOnly', url='workflow/permission'))
    def dispatch(self, request, *args, **kwargs):
        try:
            self.guidance = FormGuidance.objects.get(form="Indicator")
        except FormGuidance.DoesNotExist:
            self.guidance = None
        return super(IndicatorUpdate, self).dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super(IndicatorUpdate, self).get_context_data(**kwargs)
        context.update({'id': self.kwargs['pk']})
        getIndicator = Indicator.objects.get(id=self.kwargs['pk'])

        context.update({'i_name': getIndicator.name})
        context['programId'] = getIndicator.workflowlevel1.all()[0].id
        context['periodic_targets'] = PeriodicTarget.objects.filter(indicator=getIndicator)

        #get external service data if any
        try:
            getExternalServiceRecord = ExternalServiceRecord.objects.all().filter(indicator__id=self.kwargs['pk'])
        except ExternalServiceRecord.DoesNotExist:
            getExternalServiceRecord = None
        context.update({'getExternalServiceRecord': getExternalServiceRecord})

        return context

    # add the request to the kwargs
    def get_form_kwargs(self):
        kwargs = super(IndicatorUpdate, self).get_form_kwargs()
        kwargs['request'] = self.request
        workflowlevel1 = Indicator.objects.all().filter(id=self.kwargs['pk']).values_list("workflowlevel1__id", flat=True)
        kwargs['workflowlevel1'] = workflowlevel1
        return kwargs

    def form_invalid(self, form):
        messages.error(self.request, 'Invalid Form', fail_silently=False)
        return self.render_to_response(self.get_context_data(form=form))

    def form_valid(self, form):
        periodic_targets = self.request.POST.get('periodic_targets', None)
        indicatr = Indicator.objects.get(pk=self.kwargs.get('pk'))
        if periodic_targets:
            pt_json = json.loads(periodic_targets)
            for pt in pt_json:
                pk = int(pt.get('id'))
                if pk == 0: pk = None
                periodic_target,created = PeriodicTarget.objects.update_or_create(\
                    indicator=indicatr, id=pk,\
                    defaults={'period': pt.get('period', ''), 'target': pt.get('target', 0), 'edit_date': timezone.now() })
                #print("%s|%s = %s, %s" % (created, pk, pt.get('period'), pt.get('target') ))
                if created:
                    periodic_target.create_date = timezone.now()
                    periodic_target.save()

        self.object = form.save()
        periodic_targets = PeriodicTarget.objects.filter(indicator=indicatr).order_by('create_date')

        if self.request.is_ajax():
            data = serializers.serialize('json', [self.object])
            pts = serializers.serialize('json', periodic_targets)
            #return JsonResponse({"indicator": json.loads(data), "pts": json.loads(pts)})
            return HttpResponse("[" + data + "," + pts + "]")

        messages.success(self.request, 'Success, Indicator Updated!')
        if self.request.POST.has_key('_addanother'):
            url = "/indicators/indicator_create/"
            workflowlevel1 = self.request.POST['workflowlevel1']
            qs = workflowlevel1 + "/"
            return HttpResponseRedirect(''.join((url, qs)))

        return self.render_to_response(self.get_context_data(form=form))

    form_class = IndicatorForm


class IndicatorDelete(DeleteView):
    """
    Delete and Indicator
    """
    model = Indicator
    success_url = '/indicators/home/0/0/0/'

    @method_decorator(group_excluded('ViewOnly', url='workflow/permission'))
    def dispatch(self, request, *args, **kwargs):
        return super(IndicatorDelete, self).dispatch(request, *args, **kwargs)

    def form_invalid(self, form):

        messages.error(self.request, 'Invalid Form', fail_silently=False)

        return self.render_to_response(self.get_context_data(form=form))

    def form_valid(self, form):

        form.save()

        messages.success(self.request, 'Success, Indicator Deleted!')
        return self.render_to_response(self.get_context_data(form=form))

    form_class = IndicatorForm


class PeriodicTargetDeleteView(DeleteView):
    model = PeriodicTarget

    def delete(self, request, *args, **kwargs):
        collecteddata_count = self.get_object().collecteddata_set.count()
        if collecteddata_count > 0:
            return JsonResponse({"status": "error", "msg": "Periodic Target with data reported against it cannot be deleted."})
        #super(PeriodicTargetDeleteView).delete(request, args, kwargs)
        self.get_object().delete()
        return JsonResponse({"status": "success", "msg": "Periodic Target deleted successfully."})

class CollectedDataCreate(CreateView):
    """
    CollectedData Form
    """
    model = CollectedData
    #template_name = 'indicators/collecteddata_form.html'
    def get_template_names(self):
        if self.request.is_ajax():
            return 'indicators/collecteddata_form_modal.html'
        return 'indicators/collecteddata_form.html'

    form_class = CollectedDataForm

    @method_decorator(group_excluded('ViewOnly', url='workflow/permission'))
    def dispatch(self, request, *args, **kwargs):
        try:
            self.guidance = FormGuidance.objects.get(form="CollectedData")
        except FormGuidance.DoesNotExist:
            self.guidance = None
        return super(CollectedDataCreate, self).dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super(CollectedDataCreate, self).get_context_data(**kwargs)
        try:
            getDisaggregationLabel = DisaggregationLabel.objects.all().filter(disaggregation_type__indicator__id=self.kwargs['indicator'])
            getDisaggregationLabelStandard = DisaggregationLabel.objects.all().filter(disaggregation_type__standard=True)
        except DisaggregationLabel.DoesNotExist:
            getDisaggregationLabelStandard = None
            getDisaggregationLabel = None

        #set values to None so the form doesn't display empty fields for previous entries
        getDisaggregationValue = None

        context.update({'getDisaggregationValue': getDisaggregationValue})
        context.update({'getDisaggregationLabel': getDisaggregationLabel})
        context.update({'getDisaggregationLabelStandard': getDisaggregationLabelStandard})
        context.update({'indicator_id': self.kwargs['indicator']})
        context.update({'workflowlevel1_id': self.kwargs['workflowlevel1']})

        return context

    def get_initial(self):
        initial = {
            'indicator': self.kwargs['indicator'],
            'workflowlevel1': self.kwargs['workflowlevel1'],
        }

        return initial

    # add the request to the kwargs
    def get_form_kwargs(self):
        kwargs = super(CollectedDataCreate, self).get_form_kwargs()
        kwargs['request'] = self.request
        kwargs['workflowlevel1'] = self.kwargs['workflowlevel1']
        kwargs['indicator'] = self.kwargs['indicator']
        kwargs['tola_table'] = None

        return kwargs


    def form_invalid(self, form):

        messages.error(self.request, 'Invalid Form', fail_silently=False)

        return self.render_to_response(self.get_context_data(form=form))

    def form_valid(self, form):
        disaggregation_labels = DisaggregationLabel.objects.filter(\
                                    Q(disaggregation_type__indicator__id=self.request.POST['indicator']) | \
                                    Q(disaggregation_type__standard=True))

        # update the count with the value of Table unique count
        if form.instance.update_count_tola_table and form.instance.tola_table:
            try:
                getTable = TolaTable.objects.get(id=self.request.POST['tola_table'])
            except DisaggregationLabel.DoesNotExist:
                getTable = None
            if getTable:
                # if there is a trailing slash, remove it since TT api does not like it.
                url = getTable.url if getTable.url[-1:] != "/" else getTable.url[:-1]
                url = url if url[-5:] != "/data" else url[:-5]
                count = getTableCount(url, getTable.table_id)
            else:
                count = 0
            form.instance.achieved = count

        new = form.save()

        process_disaggregation = False

        for label in disaggregation_labels:
            if process_disaggregation == True:
                break
            for k, v in self.request.POST.iteritems():
                if k == str(label.id) and len(v) > 0:
                    process_disaggregation = True
                    break

        if process_disaggregation == True:
            for label in disaggregation_labels:
                for k, v in self.request.POST.iteritems():
                    if k == str(label.id):
                        save = new.disaggregation_value.create(disaggregation_label=label, value=v)
                        new.disaggregation_value.add(save.id)
            process_disaggregation = False


        if self.request.is_ajax():
            data = serializers.serialize('json', [new])
            return HttpResponse(data)

        messages.success(self.request, 'Success, Data Created!')

        redirect_url = '/indicators/home/0/0/0/#hidden-' + str(self.kwargs['workflowlevel1'])
        return HttpResponseRedirect(redirect_url)


class CollectedDataUpdate(UpdateView):
    """
    CollectedData Form
    """
    model = CollectedData
    #template_name = 'indicators/collecteddata_form.html'
    def get_template_names(self):
        if self.request.is_ajax():
            return 'indicators/collecteddata_form_modal.html'
        return 'indicators/collecteddata_form.html'

    @method_decorator(group_excluded('ViewOnly', url='workflow/permission'))
    def dispatch(self, request, *args, **kwargs):
        try:
            self.guidance = FormGuidance.objects.get(form="CollectedData")
        except FormGuidance.DoesNotExist:
            self.guidance = None
        return super(CollectedDataUpdate, self).dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super(CollectedDataUpdate, self).get_context_data(**kwargs)
        #get the indicator_id for the collected data
        getIndicator = CollectedData.objects.get(id=self.kwargs['pk'])

        try:
            getDisaggregationLabel = DisaggregationLabel.objects.all().filter(disaggregation_type__indicator__id=getIndicator.indicator_id)
            getDisaggregationLabelStandard = DisaggregationLabel.objects.all().filter(disaggregation_type__standard=True)
        except DisaggregationLabel.DoesNotExist:
            getDisaggregationLabel = None
            getDisaggregationLabelStandard = None

        try:
            getDisaggregationValue = DisaggregationValue.objects.all().filter(collecteddata=self.kwargs['pk']).exclude(disaggregation_label__disaggregation_type__standard=True)
            getDisaggregationValueStandard = DisaggregationValue.objects.all().filter(collecteddata=self.kwargs['pk']).filter(disaggregation_label__disaggregation_type__standard=True)
        except DisaggregationLabel.DoesNotExist:
            getDisaggregationValue = None
            getDisaggregationValueStandard = None

        context.update({'getDisaggregationLabelStandard': getDisaggregationLabelStandard})
        context.update({'getDisaggregationValueStandard': getDisaggregationValueStandard})
        context.update({'getDisaggregationValue': getDisaggregationValue})
        context.update({'getDisaggregationLabel': getDisaggregationLabel})
        context.update({'id': self.kwargs['pk']})
        context.update({'indicator_id': getIndicator.indicator_id})

        return context

    def form_invalid(self, form):
        messages.error(self.request, 'Invalid Form', fail_silently=False)
        return self.render_to_response(self.get_context_data(form=form))

    # add the request to the kwargs
    def get_form_kwargs(self):
        get_data = CollectedData.objects.get(id=self.kwargs['pk'])
        kwargs = super(CollectedDataUpdate, self).get_form_kwargs()
        kwargs['request'] = self.request
        kwargs['workflowlevel1'] = get_data.workflowlevel1
        kwargs['indicator'] = get_data.indicator
        if get_data.tola_table:
            kwargs['tola_table'] = get_data.tola_table.id
        else:
            kwargs['tola_table'] = None
        return kwargs

    def form_valid(self, form):

        getCollectedData = CollectedData.objects.get(id=self.kwargs['pk'])
        getDisaggregationLabel = DisaggregationLabel.objects.all().filter(Q(disaggregation_type__indicator__id=self.request.POST['indicator']) | Q(disaggregation_type__standard=True)).distinct()

        getIndicator = CollectedData.objects.get(id=self.kwargs['pk'])

        # update the count with the value of Table unique count
        if form.instance.update_count_tola_table and form.instance.tola_table:
            try:
                getTable = TolaTable.objects.get(id=self.request.POST['tola_table'])
            except TolaTable.DoesNotExist:
                getTable = None
            if getTable:
                # if there is a trailing slash, remove it since TT api does not like it.
                url = getTable.url if getTable.url[-1:] != "/" else getTable.url[:-1]
                url = url if url[-5:] != "/data" else url[:-5]
                count = getTableCount(url, getTable.table_id)
            else:
                count = 0
            form.instance.achieved = count

        # save the form then update manytomany relationships
        form.save()

        # Insert or update disagg values
        for label in getDisaggregationLabel:
            for key, value in self.request.POST.iteritems():
                if key == str(label.id):
                    value_to_insert = value
                    save = getCollectedData.disaggregation_value.create(disaggregation_label=label, value=value_to_insert)
                    getCollectedData.disaggregation_value.add(save.id)

        if self.request.is_ajax():
            data = serializers.serialize('json', [self.object])
            return HttpResponse(data)

        messages.success(self.request, 'Success, Data Updated!')

        redirect_url = '/indicators/home/0/0/0/#hidden-' + str(getIndicator.workflowlevel1.id)
        return HttpResponseRedirect(redirect_url)

    form_class = CollectedDataForm


class CollectedDataDelete(DeleteView):
    """
    CollectedData Delete
    """
    model = CollectedData
    success_url = '/indicators/home/0/0/0/'

    @method_decorator(group_excluded('ViewOnly', url='workflow/permission'))
    def dispatch(self, request, *args, **kwargs):
        return super(CollectedDataDelete, self).dispatch(request, *args, **kwargs)


def getTableCount(url,table_id):
    """
    Count the number of rowns in a TolaTable
    :param table_id: The TolaTable ID to update count from and return
    :return: count : count of rows from TolaTable
    """
    token = TolaSites.objects.get(site_id=1)
    if token.tola_tables_token:
        headers = {'content-type': 'application/json', 'Authorization': 'Token ' + token.tola_tables_token }
    else:
        headers = {'content-type': 'application/json'}
        print "Token Not Found"

    response = requests.get(url,headers=headers, verify=False)
    data = json.loads(response.content)
    count = None
    try:
        count = data['data_count']
        TolaTable.objects.filter(table_id = table_id).update(unique_count=count)
    except KeyError:
        pass

    return count


def merge_two_dicts(x, y):
    """
    Given two dictionary Items, merge them into a new dict as a shallow copy.
    :param x: Dict 1
    :param y: Dict 2
    :return: Merge of the 2 Dicts
    """
    z = x.copy()
    z.update(y)
    return z


def collecteddata_import(request):
    """
    Import collected data from Tola Tables
    :param request:
    :return:
    """
    owner = request.user
    #get the TolaTables URL and token from the sites object
    service = TolaSites.objects.get(site_id=1)

    # add filter to get just the users tables only
    user_filter_url = service.tola_tables_url + "&owner__username=" + str(owner)
    shared_filter_url = service.tola_tables_url + "&shared__username=" + str(owner)

    user_json = get_table(user_filter_url)
    shared_json = get_table(shared_filter_url)

    if type(shared_json) is not dict:
        data = user_json + shared_json
    else:
        data = user_json

    if request.method == 'POST':
        id = request.POST['service_table']
        filter_url = service.tola_tables_url + "&id=" + id

        data = get_table(filter_url)

        # Get Data Info
        for item in data:
            name = item['name']
            url = item['data']
            remote_owner = item['owner']['username']

        #send table ID to count items in data
        count = getTableCount(url,id)

        # get the users country
        countries = getCountry(request.user)
        check_for_existence = TolaTable.objects.all().filter(name=name,owner=owner)
        if check_for_existence:
            result = check_for_existence[0].id
        else:
            create_table = TolaTable.objects.create(name=name,owner=owner,remote_owner=remote_owner,table_id=id,url=url, unique_count=count)
            create_table.country.add(countries[0].id)
            create_table.save()
            result = create_table.id

        # send result back as json
        message = result
        return HttpResponse(json.dumps(message), content_type='application/json')

    # send the keys and vars from the json data to the template along with submitted feed info and silos for new form
    return render(request, "indicators/collecteddata_import.html", {'getTables': data})


def service_json(request,service):
    """
    For populating service indicators in dropdown
    :param service: The remote data service
    :return: JSON object of the indicators from the service
    """
    service_indicators = import_indicator(service,deserialize=False)
    return HttpResponse(service_indicators, content_type="application/json")


def tool(request):
    """
    Placeholder for Indicator planning Tool TBD
    :param request:
    :return:
    """
    return render(request, 'indicators/tool.html')


# REPORT VIEWS
def indicator_report(request, workflowlevel1=0, indicator=0, type=0):
    """
    This is the indicator library report.  List of all indicators across a country or countries filtered by
    workflowlevel1.  Lives in the "Report" navigation.
    URL: indicators/report/0/
    :param request:
    :param workflowlevel1:
    :return:
    """
    countries = getCountry(request.user)

    getPrograms = WorkflowLevel1.objects.all().filter( country__in=countries).distinct()
    getIndicatorTypes = IndicatorType.objects.all()

    filters = {}
    if int(workflowlevel1) != 0:
        filters['workflowlevel1__id'] = workflowlevel1
    if int(type) != 0:
        filters['indicator_type'] = type
    if int(indicator) != 0:
        filters['id'] = indicator
    if workflowlevel1 == 0 and type == 0:
        filters['workflowlevel1__country__in'] = countries

    indicator_data = Indicator.objects.filter(**filters)\
            .prefetch_related('sector')\
            .select_related('workflowlevel1', 'external_service_record','indicator_type',\
                'disaggregation', 'reporting_frequency')\
            .values('id','workflowlevel1__name','baseline','level__name','lop_target',\
                   'workflowlevel1__id','external_service_record__external_service__name',\
                   'key_performance_indicator','name','indicator_type__id', 'indicator_type__indicator_type',\
                   'sector__sector','disaggregation__disaggregation_type',\
                   'means_of_verification','data_collection_method',\
                   'reporting_frequency__frequency','create_date','edit_date',\
                   'source','method_of_analysis')

    data = json.dumps(list(indicator_data), cls=DjangoJSONEncoder)

    # send the keys and vars from the json data to the template along with submitted feed info and silos for new form

    return render(request, "indicators/report.html", {
                  'workflowlevel1': workflowlevel1,
                  'getPrograms': getPrograms,
                  'getIndicatorTypes': getIndicatorTypes,
                  'getIndicators': indicator_data,
                  'data': data})


def WorkflowLevel1IndicatorReport(request, workflowlevel1=0):
    """
    This is the GRID report or indicator plan for a workflowlevel1.  Shows a simple list of indicators sorted by level
    and number. Lives in the "Indicator" home page as a link.
    URL: indicators/workflowlevel1_report/[workflowlevel1_id]/
    :param request:
    :param workflowlevel1:
    :return:
    """
    workflowlevel1 = int(workflowlevel1)
    countries = getCountry(request.user)
    getPrograms = WorkflowLevel1.objects.all().filter(country__in=countries).distinct()
    getIndicators = Indicator.objects.all().filter(workflowlevel1__id=workflowlevel1).select_related().order_by('level', 'number')
    getProgram = WorkflowLevel1.objects.get(id=workflowlevel1)

    getIndicatorTypes = IndicatorType.objects.all()

    if request.method == "GET" and "search" in request.GET:
        # list1 = list()
        # for obj in filtered:
        #    list1.append(obj)
        getIndicators = Indicator.objects.all().filter(
            Q(indicator_type__icontains=request.GET["search"]) |
            Q(name__icontains=request.GET["search"]) |
            Q(number__icontains=request.GET["search"]) |
            Q(definition__startswith=request.GET["search"])
        ).filter(workflowlevel1__id=workflowlevel1).select_related().order_by('level', 'number')

    # send the keys and vars from the json data to the template along with submitted feed info and silos for new form
    return render(request, "indicators/grid_report.html", {'getIndicators': getIndicators, 'getPrograms': getPrograms,
                                                           'getProgram': getProgram, 'form': None,
                                                           'helper': None,
                                                           'getIndicatorTypes': getIndicatorTypes})


def indicator_data_report(request, id=0, workflowlevel1=0, type=0):
    """
    This is the Indicator Visual report for each indicator and workflowlevel1.  Displays a list collected data entries
    and sums it at the bottom.  Lives in the "Reports" navigation.
    URL: indicators/data/[id]/[workflowlevel1]/[type]
    :param request:
    :param id: Indicator ID
    :param workflowlevel1: Program ID
    :param type: Type ID
    :return:
    """
    countries = getCountry(request.user)
    getPrograms = WorkflowLevel1.objects.all().filter(country__in=countries).distinct()
    getIndicators = Indicator.objects.select_related().filter(workflowlevel1__country__in=countries)
    getTypes = IndicatorType.objects.all()
    indicator_name = None
    workflowlevel1_name = None
    type_name = None
    q = {'indicator__id__isnull': False}
    z = None

    # Build query based on filters and search
    if int(id) != 0:
        getSiteProfile = Indicator.objects.all().filter(id=id).select_related()
        indicator_name = Indicator.objects.get(id=id).name
        z = {
            'indicator__id': id
        }
    else:
        getSiteProfile = SiteProfile.objects.all().select_related()
        z = {
            'indicator__workflowlevel1__country__in': countries,
        }

    if int(workflowlevel1) != 0:
        getSiteProfile = SiteProfile.objects.all().filter(projectagreement__workflowlevel1__id=workflowlevel1).select_related()
        workflowlevel1_name = WorkflowLevel1.objects.get(id=workflowlevel1).name
        q = {
            'workflowlevel1__id': workflowlevel1
        }
        # redress the indicator list based on workflowlevel1
        getIndicators = Indicator.objects.select_related().filter(workflowlevel1=workflowlevel1)

    if int(type) != 0:
        type_name = IndicatorType.objects.get(id=type).indicator_type
        q = {
            'indicator__indicator_type__id': type,
        }

    if z:
        q.update(z)

    if request.method == "GET" and "search" in request.GET:
        queryset = CollectedData.objects.filter(**q).filter(
            Q(workflowlevel2__project_name__contains=request.GET["search"]) |
            Q(description__icontains=request.GET["search"]) |
            Q(indicator__name__contains=request.GET["search"])
        ).select_related()
    else:

        queryset = CollectedData.objects.all().filter(**q).select_related()


    # send the keys and vars from the json data to the template along with submitted feed info and silos for new form
    return render(request, "indicators/data_report.html",
                  {'getQuantitativeData': queryset, 'countries': countries, 'getSiteProfile': getSiteProfile,
                   'getPrograms': getPrograms, 'getIndicators': getIndicators,
                   'getTypes': getTypes, 'form': None, 'helper': None,
                   'id': id, 'workflowlevel1': workflowlevel1, 'type': type, 'indicator': id, 'indicator_name': indicator_name,
                   'type_name': type_name, 'workflowlevel1_name': workflowlevel1_name})


def dictfetchall(cursor):
    "Return all rows from a cursor as a dict"
    columns = [col[0] for col in cursor.description]
    return [
        dict(zip(columns, row))
        for row in cursor.fetchall()
    ]


class DisaggregationReportMixin(object):
    def get_context_data(self, **kwargs):
        context = super(DisaggregationReportMixin, self).get_context_data(**kwargs)

        countries = getCountry(self.request.user)
        programs = WorkflowLevel1.objects.filter(country__in=countries).distinct()
        indicators = Indicator.objects.filter(workflowlevel1__country__in=countries)

        program_selected = WorkflowLevel1.objects.filter(id=kwargs.get('workflowlevel1', None)).first()
        if not program_selected:
            program_selected = programs.first()

        if program_selected:
            if program_selected.indicator_set.count() > 0:
                indicators = indicators.filter(workflowlevel1=program_selected.id)

        disagg_query = "SELECT i.id AS IndicatorID, dt.disaggregation_type AS DType, "\
            "l.customsort AS customsort, l.label AS Disaggregation, SUM(dv.value) AS Actuals "\
                "FROM indicators_collecteddata_disaggregation_value AS cdv "\
                "INNER JOIN indicators_collecteddata AS c ON c.id = cdv.collecteddata_id "\
                "INNER JOIN indicators_indicator AS i ON i.id = c.indicator_id "\
                "INNER JOIN indicators_indicator_workflowlevel1 AS ip ON ip.indicator_id = i.id "\
                "INNER JOIN workflow_workflowlevel1 AS p ON p.id = ip.workflowlevel1_id "\
                "INNER JOIN indicators_disaggregationvalue AS dv ON dv.id = cdv.disaggregationvalue_id "\
                "INNER JOIN indicators_disaggregationlabel AS l ON l.id = dv.disaggregation_label_id "\
                "INNER JOIN indicators_disaggregationtype AS dt ON dt.id = l.disaggregation_type_id "\
                "WHERE p.id = %s "\
                "GROUP BY IndicatorID, DType, customsort, Disaggregation "\
                "ORDER BY IndicatorID, DType, customsort, Disaggregation;"  % program_selected.id
        # we need to limit this catch exception but we should fix the manual sql query first
        try:
            cursor = connection.cursor()
            cursor.execute(disagg_query)
            disdata = dictfetchall(cursor)
        except:
            disdata = {}


        indicator_query = "SELECT DISTINCT p.id as PID, i.id AS IndicatorID, i.number AS INumber, i.name AS Indicator, "\
            "i.lop_target AS LOP_Target, SUM(cd.achieved) AS Overall "\
            "FROM indicators_indicator AS i "\
            "INNER JOIN indicators_indicator_workflowlevel1 AS ip ON ip.indicator_id = i.id "\
            "INNER JOIN workflow_workflowlevel1 AS p ON p.id = ip.workflowlevel1_id "\
            "LEFT OUTER JOIN indicators_collecteddata AS cd ON i.id = cd.indicator_id "\
            "WHERE p.id = %s "\
            "GROUP BY PID, IndicatorID "\
            "ORDER BY Indicator; " % program_selected.id
        cursor.execute(indicator_query)
        idata = dictfetchall(cursor)

        for indicator in idata:
            indicator["disdata"] = []
            for i, dis in enumerate(disdata):
                if dis['IndicatorID'] == indicator['IndicatorID']:
                    indicator["disdata"].append(disdata[i])


        context['data'] = idata
        context['getPrograms'] = programs
        context['getIndicators'] = indicators
        context['program_selected'] = program_selected
        return context

class DisaggregationReport(DisaggregationReportMixin, TemplateView):
    template_name = 'indicators/disaggregation_report.html'

    def get_context_data(self, **kwargs):
        context = super(DisaggregationReport, self).get_context_data(**kwargs)
        context['disaggregationprint_button'] = True
        return context


class DisaggregationPrint(DisaggregationReportMixin, TemplateView):
    template_name = 'indicators/disaggregation_print.html'


    def get(self, request, *args, **kwargs):
        context = super(DisaggregationPrint, self).get_context_data(**kwargs)
        hmtl_string = render(request, self.template_name, {'data': context['data'], 'program_selected': context['program_selected']})
        pdffile = HTML(string=hmtl_string.content)

        result = pdffile.write_pdf(stylesheets=[CSS(
            string='@page {\
                size: letter; margin: 1cm;\
                @bottom-right{\
                    content: "Page " counter(page) " of " counter(pages);\
                };\
            }'\
        )])
        res = HttpResponse(result, content_type='application/pdf')
        res['Content-Disposition'] = 'attachment; filename=indicators_disaggregation_report.pdf'
        res['Content-Transfer-Encoding'] = 'binary'
        #return super(DisaggregationReport, self).get(request, *args, **kwargs)
        return res


class TVAPrint(TemplateView):
    template_name = 'indicators/tva_print.html'

    def get(self, request, *args, **kwargs):
        program = Program.objects.filter(id=kwargs.get('program', None)).first()
        indicators = Indicator.objects\
            .select_related('sector')\
            .prefetch_related('indicator_type', 'level', 'program')\
            .filter(program=program)\
            .annotate(actuals=Sum('collecteddata__achieved'))

        hmtl_string = render(request, 'indicators/tva_print.html', {'data': indicators, 'program': program})
        pdffile = HTML(string=hmtl_string.content)
        result = pdffile.write_pdf(stylesheets=[CSS(
            string='@page {\
                size: letter; margin: 1cm;\
                @bottom-right{\
                    content: "Page " counter(page) " of " counter(pages);\
                };\
            }'
        )])
        res = HttpResponse(result, content_type='application/pdf')
        res['Content-Disposition'] = 'attachment; filename=tva.pdf'
        res['Content-Transfer-Encoding'] = 'binary'
        return res

class TVAReport(TemplateView):
    template_name = 'indicators/tva_report.html'

    def get_context_data(self, **kwargs):
        context = super(TVAReport, self).get_context_data(**kwargs)
        countries = getCountry(self.request.user)
        filters = {'workflowlevel1__country__in': countries}
        workflowlevel1 = WorkflowLevel1.objects.filter(id=kwargs.get('workflowlevel1', None)).first()
        indicator_type = IndicatorType.objects.filter(id=kwargs.get('type', None)).first()
        indicator = Indicator.objects.filter(id=kwargs.get('indicator', None)).first()

        if workflowlevel1:
            filters['workflowlevel1'] = workflowlevel1.pk
        if indicator_type:
            filters['indicator__indicator_type__id'] = indicator_type.pk
        if indicator:
            filters['indicator'] = indicator.pk

        indicators = Indicator.objects\
            .select_related('sector')\
            .prefetch_related('indicator_type', 'level', 'workflowlevel1')\
            .filter(**filters)\
            .annotate(actuals=Sum('collecteddata__achieved'))
            #.annotate(actuals=Sum('collecteddata__disaggregation_value__value'))
        context['data'] = indicators
        context['getIndicators'] = Indicator.objects.filter(workflowlevel1__country__in=countries).exclude(collecteddata__isnull=True)
        context['getPrograms'] = WorkflowLevel1.objects.filter(country__in=countries).distinct()
        context['getIndicatorTypes'] = IndicatorType.objects.all()

        """
        WHY IS THIS REPEASE AND WITH A HARDCODED WORKFLOW NAME?  SHOULD BE REMOVED GWL
        indicators = Indicator.objects.filter(workflowlevel1=223)\
            .annotate(actuals=Sum('collecteddata__disaggregation_value__value'))\
            #.values('actuals', 'number', 'name', 'indicator_type__indicator_type')
        """
        context['program'] = workflowlevel1
        context['export_to_pdf_url'] = True
        return context


class CollectedDataList(ListView):
    """
    This is the Indicator CollectedData report for each indicator and workflowlevel1.  Displays a list collected data entries
    and sums it at the bottom.  Lives in the "Reports" navigation.
    URL: indicators/data/[id]/[workflowlevel1]/[type]
    :param request:
    :param indicator: Indicator ID
    :param workflowlevel1: Program ID
    :param type: Type ID
    :return:
    """
    model = CollectedData
    template_name = 'indicators/collecteddata_list.html'

    def get(self, request, *args, **kwargs):

        countries = getCountry(request.user)
        getPrograms = WorkflowLevel1.objects.all().filter(country__in=countries).distinct()

        getIndicators = Indicator.objects.all()\
            .filter(workflowlevel1__country__in=countries)\
            .exclude(collecteddata__isnull=True)

        workflowlevel1 = self.kwargs['workflowlevel1']
        indicator = self.kwargs['indicator']
        type = self.kwargs['type']
        indicator_name = ""
        type_name = ""
        workflowlevel1_name = ""


        if self.request.GET.get('export'):
            dataset = CollectedDataResource().export(indicators)
            response = HttpResponse(dataset.csv, content_type='application/ms-excel')
            response['Content-Disposition'] = 'attachment; filename=indicator_data.csv'
            return response

        return render(request, self.template_name, {'getPrograms': getPrograms,
                                                    'getIndicators': getIndicators,
                                                    'workflowlevel1': workflowlevel1, 'indicator': indicator, 'type': type,
                                                    'filter_workflowlevel1': workflowlevel1_name, 'filter_indicator': indicator_name,
                                                    'indicator': indicator, 'workflowlevel1': workflowlevel1,
                                                    'indicator_name': indicator_name,
                                                    'workflowlevel1_name': workflowlevel1_name, 'type_name': type_name})


class IndicatorExport(View):
    """
    Export all indicators to a CSV file
    """
    def get(self, request, *args, **kwargs ):


        if int(kwargs['id']) == 0:
            del kwargs['id']
        if int(kwargs['indicator_type']) == 0:
            del kwargs['indicator_type']
        if int(kwargs['workflowlevel1']) == 0:
            del kwargs['workflowlevel1']

        countries = getCountry(request.user)

        queryset = Indicator.objects.filter(**kwargs).filter(workflowlevel1__country__in=countries)


        indicator = IndicatorResource().export(queryset)
        response = HttpResponse(indicator.csv, content_type='application/ms-excel')
        response['Content-Disposition'] = 'attachment; filename=indicator.csv'
        return response


class IndicatorDataExport(View):
    """
    Export all indicators to a CSV file
    """
    def get(self, request, *args, **kwargs ):

        if int(kwargs['indicator']) == 0:
            del kwargs['indicator']
        if int(kwargs['workflowlevel1']) == 0:
            del kwargs['workflowlevel1']
        if int(kwargs['type']) == 0:
            del kwargs['type']
        else:
           kwargs['indicator__indicator_type__id'] = kwargs['type']
           del kwargs['type']

        countries = getCountry(request.user)

        queryset = CollectedData.objects.filter(**kwargs).filter(indicator__workflowlevel1__country__in=countries)
        dataset = CollectedDataResource().export(queryset)
        response = HttpResponse(dataset.csv, content_type='application/ms-excel')
        response['Content-Disposition'] = 'attachment; filename=indicator_data.csv'
        return response


class CountryExport(View):

    def get(self, *args, **kwargs ):
        country = CountryResource().export()
        response = HttpResponse(country.csv, content_type="csv")
        response['Content-Disposition'] = 'attachment; filename=country.csv'
        return response

def const_table_det_url(url):
    url_data = urlparse(url)
    root = url_data.scheme
    org_host = url_data.netloc
    path = url_data.path
    components = re.split('/', path)

    s = []
    for c in components:
        s.append(c)

    new_url = str(root)+'://'+str(org_host)+'/silo_detail/'+str(s[3])+'/'

    return new_url