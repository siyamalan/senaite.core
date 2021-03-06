# -*- coding: utf-8 -*-
#
# This file is part of SENAITE.CORE.
#
# SENAITE.CORE is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, version 2.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this program; if not, write to the Free Software Foundation, Inc., 51
# Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
# Copyright 2018-2019 by it's authors.
# Some rights reserved, see README and LICENSE.

import json
from collections import OrderedDict
from datetime import datetime

from bika.lims import POINTS_OF_CAPTURE
from bika.lims import api
from bika.lims import bikaMessageFactory as _
from bika.lims import logger
from bika.lims.api.analysisservice import get_calculation_dependencies_for
from bika.lims.api.analysisservice import get_service_dependencies_for
from bika.lims.interfaces import IGetDefaultFieldValueARAddHook
from bika.lims.utils import tmpID
from bika.lims.utils.analysisrequest import create_analysisrequest as crar
from bika.lims.workflow import ActionHandlerPool
from BTrees.OOBTree import OOBTree
from DateTime import DateTime
from plone import protect
from plone.memoize.volatile import DontCache
from plone.memoize.volatile import cache
from Products.CMFPlone.utils import _createObjectByType
from Products.CMFPlone.utils import safe_unicode
from Products.Five.browser import BrowserView
from Products.Five.browser.pagetemplatefile import ViewPageTemplateFile
from zope.annotation.interfaces import IAnnotations
from zope.component import queryAdapter
from zope.i18n.locales import locales
from zope.interface import implements
from zope.publisher.interfaces import IPublishTraverse

AR_CONFIGURATION_STORAGE = "bika.lims.browser.analysisrequest.manage.add"
SKIP_FIELD_ON_COPY = ["Sample", "PrimaryAnalysisRequest", "Remarks"]


def returns_json(func):
    """Decorator for functions which return JSON
    """
    def decorator(*args, **kwargs):
        instance = args[0]
        request = getattr(instance, 'request', None)
        request.response.setHeader("Content-Type", "application/json")
        result = func(*args, **kwargs)
        return json.dumps(result)
    return decorator


def cache_key(method, self, obj):
    if obj is None:
        raise DontCache
    return api.get_cache_key(obj)


class AnalysisRequestAddView(BrowserView):
    """AR Add view
    """
    template = ViewPageTemplateFile("templates/ar_add2.pt")

    def __init__(self, context, request):
        super(AnalysisRequestAddView, self).__init__(context, request)
        self.request = request
        self.context = context
        self.fieldvalues = {}
        self.tmp_ar = None

    def __call__(self):
        self.portal = api.get_portal()
        self.portal_url = self.portal.absolute_url()
        self.bika_setup = api.get_bika_setup()
        self.request.set('disable_plone.rightcolumn', 1)
        self.came_from = "add"
        self.tmp_ar = self.get_ar()
        self.ar_count = self.get_ar_count()
        self.fieldvalues = self.generate_fieldvalues(self.ar_count)
        self.specifications = self.generate_specifications(self.ar_count)
        self.ShowPrices = self.bika_setup.getShowPrices()
        self.icon = self.portal_url + \
            "/++resource++bika.lims.images/sample_big.png"
        logger.info("*** Prepared data for {} ARs ***".format(self.ar_count))
        return self.template()

    def get_view_url(self):
        """Return the current view url including request parameters
        """
        request = self.request
        url = request.getURL()
        qs = request.getHeader("query_string")
        if not qs:
            return url
        return "{}?{}".format(url, qs)

    def get_object_by_uid(self, uid):
        """Get the object by UID
        """
        logger.debug("get_object_by_uid::UID={}".format(uid))
        obj = api.get_object_by_uid(uid, None)
        if obj is None:
            logger.warn("!! No object found for UID #{} !!")
        return obj

    def get_currency(self):
        """Returns the configured currency
        """
        bika_setup = api.get_bika_setup()
        currency = bika_setup.getCurrency()
        currencies = locales.getLocale('en').numbers.currencies
        return currencies[currency]

    def is_ar_specs_allowed(self):
        """Checks if AR Specs are allowed
        """
        bika_setup = api.get_bika_setup()
        return bika_setup.getEnableARSpecs()

    def get_ar_count(self):
        """Return the ar_count request paramteter
        """
        ar_count = 1
        try:
            ar_count = int(self.request.form.get("ar_count", 1))
        except (TypeError, ValueError):
            ar_count = 1
        return ar_count

    def get_ar(self):
        """Create a temporary AR to fetch the fields from
        """
        if not self.tmp_ar:
            logger.info("*** CREATING TEMPORARY AR ***")
            self.tmp_ar = self.context.restrictedTraverse(
                "portal_factory/AnalysisRequest/Request new analyses")
        return self.tmp_ar

    def get_ar_schema(self):
        """Return the AR schema
        """
        logger.info("*** GET AR SCHEMA ***")
        ar = self.get_ar()
        return ar.Schema()

    def get_ar_fields(self):
        """Return the AR schema fields (including extendend fields)
        """
        logger.info("*** GET AR FIELDS ***")
        schema = self.get_ar_schema()
        return schema.fields()

    def get_fieldname(self, field, arnum):
        """Generate a new fieldname with a '-<arnum>' suffix
        """
        name = field.getName()
        # ensure we have only *one* suffix
        base_name = name.split("-")[0]
        suffix = "-{}".format(arnum)
        return "{}{}".format(base_name, suffix)

    def generate_specifications(self, count=1):
        """Returns a mapping of count -> specification
        """

        out = {}

        # mapping of UID index to AR objects {1: <AR1>, 2: <AR2> ...}
        copy_from = self.get_copy_from()

        for arnum in range(count):
            # get the source object
            source = copy_from.get(arnum)

            if source is None:
                out[arnum] = {}
                continue

            # get the results range from the source object
            results_range = source.getResultsRange()

            # mapping of keyword -> rr specification
            specification = {}
            for rr in results_range:
                specification[rr.get("keyword")] = rr
            out[arnum] = specification

        return out

    def get_input_widget(self, fieldname, arnum=0, **kw):
        """Get the field widget of the AR in column <arnum>

        :param fieldname: The base fieldname
        :type fieldname: string
        """

        # temporary AR Context
        context = self.get_ar()
        # request = self.request
        schema = context.Schema()

        # get original field in the schema from the base_fieldname
        base_fieldname = fieldname.split("-")[0]
        field = context.getField(base_fieldname)

        # fieldname with -<arnum> suffix
        new_fieldname = self.get_fieldname(field, arnum)
        new_field = field.copy(name=new_fieldname)

        # get the default value for this field
        fieldvalues = self.fieldvalues
        field_value = fieldvalues.get(new_fieldname)
        # request_value = request.form.get(new_fieldname)
        # value = request_value or field_value
        value = field_value

        def getAccessor(instance):
            def accessor(**kw):
                return value
            return accessor

        # inject the new context for the widget renderer
        # see: Products.Archetypes.Renderer.render
        kw["here"] = context
        kw["context"] = context
        kw["fieldName"] = new_fieldname

        # make the field available with this name
        # XXX: This is a hack to make the widget available in the template
        schema._fields[new_fieldname] = new_field
        new_field.getAccessor = getAccessor

        # set the default value
        form = dict()
        form[new_fieldname] = value
        self.request.form.update(form)
        logger.info("get_input_widget: fieldname={} arnum={} "
                    "-> new_fieldname={} value={}".format(
                        fieldname, arnum, new_fieldname, value))
        widget = context.widget(new_fieldname, **kw)
        return widget

    def get_copy_from(self):
        """Returns a mapping of UID index -> AR object
        """
        # Create a mapping of source ARs for copy
        copy_from = self.request.form.get("copy_from", "").split(",")
        # clean out empty strings
        copy_from_uids = filter(lambda x: x, copy_from)
        out = dict().fromkeys(range(len(copy_from_uids)))
        for n, uid in enumerate(copy_from_uids):
            ar = self.get_object_by_uid(uid)
            if ar is None:
                continue
            out[n] = ar
        logger.info("get_copy_from: uids={}".format(copy_from_uids))
        return out

    def get_default_value(self, field, context, arnum):
        """Get the default value of the field
        """
        name = field.getName()
        default = field.getDefault(context)
        if name == "Batch":
            batch = self.get_batch()
            if batch is not None:
                default = batch
        if name == "Client":
            client = self.get_client()
            if client is not None:
                default = client
        # only set default contact for first column
        if name == "Contact" and arnum == 0:
            contact = self.get_default_contact()
            if contact is not None:
                default = contact
        if name == "Sample":
            sample = self.get_sample()
            if sample is not None:
                default = sample
        # Querying for adapters to get default values from add-ons':
        # We don't know which fields the form will render since
        # some of them may come from add-ons. In order to obtain the default
        # value for those fields we take advantage of adapters. Adapters
        # registration should have the following format:
        # < adapter
        #   factory = ...
        #   for = "*"
        #   provides = "bika.lims.interfaces.IGetDefaultFieldValueARAddHook"
        #   name = "<fieldName>_default_value_hook"
        # / >
        hook_name = name + '_default_value_hook'
        adapter = queryAdapter(
            self.request,
            name=hook_name,
            interface=IGetDefaultFieldValueARAddHook)
        if adapter is not None:
            default = adapter(self.context)
        logger.info("get_default_value: context={} field={} value={} arnum={}"
                    .format(context, name, default, arnum))
        return default

    def get_field_value(self, field, context):
        """Get the stored value of the field
        """
        name = field.getName()
        value = context.getField(name).get(context)
        logger.info("get_field_value: context={} field={} value={}".format(
            context, name, value))
        return value

    def get_client(self):
        """Returns the Client
        """
        context = self.context
        parent = api.get_parent(context)
        if context.portal_type == "Client":
            return context
        elif parent.portal_type == "Client":
            return parent
        elif context.portal_type == "Batch":
            return context.getClient()
        elif parent.portal_type == "Batch":
            return context.getClient()
        return None

    def get_sample(self):
        """Returns the Sample
        """
        context = self.context
        if context.portal_type == "Sample":
            return context
        return None

    def get_batch(self):
        """Returns the Batch
        """
        context = self.context
        parent = api.get_parent(context)
        if context.portal_type == "Batch":
            return context
        elif parent.portal_type == "Batch":
            return parent
        return None

    def get_parent_ar(self, ar):
        """Returns the parent AR
        """
        parent = ar.getParentAnalysisRequest()

        # Return immediately if we have no parent
        if parent is None:
            return None

        # Walk back the chain until we reach the source AR
        while True:
            pparent = parent.getParentAnalysisRequest()
            if pparent is None:
                break
            # remember the new parent
            parent = pparent

        return parent

    def generate_fieldvalues(self, count=1):
        """Returns a mapping of '<fieldname>-<count>' to the default value
        of the field or the field value of the source AR
        """
        ar_context = self.get_ar()

        # mapping of UID index to AR objects {1: <AR1>, 2: <AR2> ...}
        copy_from = self.get_copy_from()

        out = {}
        # the original schema fields of an AR (including extended fields)
        fields = self.get_ar_fields()

        # generate fields for all requested ARs
        for arnum in range(count):
            source = copy_from.get(arnum)
            parent = None
            if source is not None:
                parent = self.get_parent_ar(source)
            for field in fields:
                value = None
                fieldname = field.getName()
                if source and fieldname not in SKIP_FIELD_ON_COPY:
                    # get the field value stored on the source
                    context = parent or source
                    value = self.get_field_value(field, context)
                else:
                    # get the default value of this field
                    value = self.get_default_value(
                        field, ar_context, arnum=arnum)
                # store the value on the new fieldname
                new_fieldname = self.get_fieldname(field, arnum)
                out[new_fieldname] = value

        return out

    def get_default_contact(self, client=None):
        """Logic refactored from JavaScript:

        * If client only has one contact, and the analysis request comes from
        * a client, then Auto-complete first Contact field.
        * If client only has one contect, and the analysis request comes from
        * a batch, then Auto-complete all Contact field.

        :returns: The default contact for the AR
        :rtype: Client object or None
        """
        catalog = api.get_tool("portal_catalog")
        client = client or self.get_client()
        path = api.get_path(self.context)
        if client:
            path = api.get_path(client)
        query = {
            "portal_type": "Contact",
            "path": {
                "query": path,
                "depth": 1
            },
            "incactive_state": "active",
        }
        contacts = catalog(query)
        if len(contacts) == 1:
            return api.get_object(contacts[0])
        return None

    def getMemberDiscountApplies(self):
        """Return if the member discount applies for this client

        :returns: True if member discount applies for the client
        :rtype: bool
        """
        client = self.get_client()
        if client is None:
            return False
        return client.getMemberDiscountApplies()

    def is_field_visible(self, field):
        """Check if the field is visible
        """
        context = self.context
        fieldname = field.getName()

        # hide the Client field on client and batch contexts
        if fieldname == "Client" and context.portal_type in ("Client", ):
            return False

        # hide the Batch field on batch contexts
        if fieldname == "Batch" and context.portal_type in ("Batch", ):
            return False

        return True

    def get_fields_with_visibility(self, visibility, mode="add"):
        """Return the AR fields with the current visibility
        """
        ar = self.get_ar()
        mv = api.get_view("ar_add_manage", context=ar)
        mv.get_field_order()

        out = []
        for field in mv.get_fields_with_visibility(visibility, mode):
            # check custom field condition
            visible = self.is_field_visible(field)
            if visible is False and visibility != "hidden":
                continue
            out.append(field)
        return out

    def get_service_categories(self, restricted=True):
        """Return all service categories in the right order

        :param restricted: Client settings restrict categories
        :type restricted: bool
        :returns: Category catalog results
        :rtype: brains
        """
        bsc = api.get_tool("bika_setup_catalog")
        query = {
            "portal_type": "AnalysisCategory",
            "is_active": True,
            "sort_on": "sortable_title",
        }
        categories = bsc(query)
        client = self.get_client()
        if client and restricted:
            restricted_categories = client.getRestrictedCategories()
            restricted_category_ids = map(
                lambda c: c.getId(), restricted_categories)
            # keep correct order of categories
            if restricted_category_ids:
                categories = filter(
                    lambda c: c.getId in restricted_category_ids, categories)
        return categories

    def get_points_of_capture(self):
        items = POINTS_OF_CAPTURE.items()
        return OrderedDict(items)

    def get_services(self, poc="lab"):
        """Return all Services

        :param poc: Point of capture (lab/field)
        :type poc: string
        :returns: Mapping of category -> list of services
        :rtype: dict
        """
        bsc = api.get_tool("bika_setup_catalog")
        query = {
            "portal_type": "AnalysisService",
            "getPointOfCapture": poc,
            "is_active": True,
            "sort_on": "sortable_title",
        }
        services = bsc(query)
        categories = self.get_service_categories(restricted=False)
        analyses = {key: [] for key in map(lambda c: c.Title, categories)}

        # append the empty category as well
        analyses[""] = []

        for brain in services:
            category = brain.getCategoryTitle
            if category in analyses:
                analyses[category].append(brain)
        return analyses

    @cache(cache_key)
    def get_service_uid_from(self, analysis):
        """Return the service from the analysis
        """
        analysis = api.get_object(analysis)
        return api.get_uid(analysis.getAnalysisService())

    def is_service_selected(self, service):
        """Checks if the given service is selected by one of the ARs.
        This is used to make the whole line visible or not.
        """
        service_uid = api.get_uid(service)
        for arnum in range(self.ar_count):
            analyses = self.fieldvalues.get("Analyses-{}".format(arnum))
            if not analyses:
                continue
            service_uids = map(self.get_service_uid_from, analyses)
            if service_uid in service_uids:
                return True
        return False


class AnalysisRequestManageView(BrowserView):
    """AR Manage View
    """
    template = ViewPageTemplateFile("templates/ar_add_manage.pt")

    def __init__(self, context, request):
        self.context = context
        self.request = request
        self.tmp_ar = None

    def __call__(self):
        protect.CheckAuthenticator(self.request.form)
        form = self.request.form
        if form.get("submitted", False) and form.get("save", False):
            order = form.get("order")
            self.set_field_order(order)
            visibility = form.get("visibility")
            self.set_field_visibility(visibility)
        if form.get("submitted", False) and form.get("reset", False):
            self.flush()
        return self.template()

    def get_ar(self):
        if not self.tmp_ar:
            self.tmp_ar = self.context.restrictedTraverse(
                "portal_factory/AnalysisRequest/Request new analyses")
        return self.tmp_ar

    def get_annotation(self):
        bika_setup = api.get_bika_setup()
        return IAnnotations(bika_setup)

    @property
    def storage(self):
        annotation = self.get_annotation()
        if annotation.get(AR_CONFIGURATION_STORAGE) is None:
            annotation[AR_CONFIGURATION_STORAGE] = OOBTree()
        return annotation[AR_CONFIGURATION_STORAGE]

    def flush(self):
        annotation = self.get_annotation()
        if annotation.get(AR_CONFIGURATION_STORAGE) is not None:
            del annotation[AR_CONFIGURATION_STORAGE]

    def set_field_order(self, order):
        self.storage.update({"order": order})

    def get_field_order(self):
        order = self.storage.get("order")
        if order is None:
            return map(lambda f: f.getName(), self.get_fields())
        return order

    def set_field_visibility(self, visibility):
        self.storage.update({"visibility": visibility})

    def get_field_visibility(self):
        return self.storage.get("visibility")

    def is_field_visible(self, field):
        if field.required:
            return True
        visibility = self.get_field_visibility()
        if visibility is None:
            return True
        return visibility.get(field.getName(), True)

    def get_field(self, name):
        """Get AR field by name
        """
        ar = self.get_ar()
        return ar.getField(name)

    def get_fields(self):
        """Return all AR fields
        """
        ar = self.get_ar()
        return ar.Schema().fields()

    def get_sorted_fields(self):
        """Return the sorted fields
        """
        inf = float("inf")
        order = self.get_field_order()

        def field_cmp(field1, field2):
            _n1 = field1.getName()
            _n2 = field2.getName()
            _i1 = _n1 in order and order.index(_n1) + 1 or inf
            _i2 = _n2 in order and order.index(_n2) + 1 or inf
            return cmp(_i1, _i2)

        return sorted(self.get_fields(), cmp=field_cmp)

    def get_fields_with_visibility(self, visibility="edit", mode="add"):
        """Return the fields with visibility
        """
        fields = self.get_sorted_fields()

        out = []

        for field in fields:
            v = field.widget.isVisible(
                self.context, mode, default='invisible', field=field)

            if self.is_field_visible(field) is False:
                v = "hidden"

            visibility_guard = True
            # visibility_guard is a widget field defined in the schema in order
            # to know the visibility of the widget when the field is related to
            # a dynamically changing content such as workflows. For instance
            # those fields related to the workflow will be displayed only if
            # the workflow is enabled, otherwise they should not be shown.
            if 'visibility_guard' in dir(field.widget):
                visibility_guard = eval(field.widget.visibility_guard)
            if v == visibility and visibility_guard:
                out.append(field)

        return out


class ajaxAnalysisRequestAddView(AnalysisRequestAddView):
    """Ajax helpers for the analysis request add form
    """
    implements(IPublishTraverse)

    def __init__(self, context, request):
        super(ajaxAnalysisRequestAddView, self).__init__(context, request)
        self.context = context
        self.request = request
        self.traverse_subpath = []
        # Errors are aggregated here, and returned together to the browser
        self.errors = {}

    def publishTraverse(self, request, name):
        """ get's called before __call__ for each path name
        """
        self.traverse_subpath.append(name)
        return self

    @returns_json
    def __call__(self):
        """Dispatch the path to a method and return JSON.
        """
        protect.CheckAuthenticator(self.request.form)
        protect.PostOnly(self.request.form)

        if len(self.traverse_subpath) != 1:
            return self.error("Not found", status=404)
        func_name = "ajax_{}".format(self.traverse_subpath[0])
        func = getattr(self, func_name, None)
        if func is None:
            return self.error("Invalid function", status=400)
        return func()

    def error(self, message, status=500, **kw):
        """Set a JSON error object and a status to the response
        """
        self.request.response.setStatus(status)
        result = {"success": False, "errors": message}
        result.update(kw)
        return result

    def to_iso_date(self, dt):
        """Return the ISO representation of a date object
        """
        if dt is None:
            return ""
        if isinstance(dt, DateTime):
            return dt.ISO8601()
        if isinstance(dt, datetime):
            return dt.isoformat()
        raise TypeError("{} is neiter an instance of DateTime nor datetime"
                        .format(repr(dt)))

    def get_records(self):
        """Returns a list of AR records

        Fields coming from `request.form` have a number prefix, e.g. Contact-0.
        Fields with the same suffix number are grouped together in a record.
        Each record represents the data for one column in the AR Add form and
        contains a mapping of the fieldName (w/o prefix) -> value.

        Example:
        [{"Contact": "Rita Mohale", ...}, {Contact: "Neil Standard"} ...]
        """
        form = self.request.form
        ar_count = self.get_ar_count()

        records = []
        # Group belonging AR fields together
        for arnum in range(ar_count):
            record = {}
            s1 = "-{}".format(arnum)
            keys = filter(lambda key: s1 in key, form.keys())
            for key in keys:
                new_key = key.replace(s1, "")
                value = form.get(key)
                record[new_key] = value
            records.append(record)
        return records

    def get_uids_from_record(self, record, key):
        """Returns a list of parsed UIDs from a single form field identified by
        the given key.

        A form field ending with `_uid` can contain an empty value, a
        single UID or multiple UIDs separated by a comma.

        This method parses the UID value and returns a list of non-empty UIDs.
        """
        value = record.get(key, None)
        if value is None:
            return []
        if isinstance(value, basestring):
            value = value.split(",")
        return filter(lambda uid: uid, value)

    def get_objs_from_record(self, record, key):
        """Returns a mapping of UID -> object
        """
        uids = self.get_uids_from_record(record, key)
        objs = map(self.get_object_by_uid, uids)
        return dict(zip(uids, objs))

    @cache(cache_key)
    def get_base_info(self, obj):
        """Returns the base info of an object
        """
        if obj is None:
            return {}

        info = {
            "id": obj.getId(),
            "uid": obj.UID(),
            "title": obj.Title(),
            "description": obj.Description(),
            "url": obj.absolute_url(),
        }

        return info

    @cache(cache_key)
    def get_client_info(self, obj):
        """Returns the client info of an object
        """
        info = self.get_base_info(obj)

        default_contact_info = {}
        default_contact = self.get_default_contact(client=obj)
        if default_contact:
            default_contact_info = self.get_contact_info(default_contact)

        info.update({
            "default_contact": default_contact_info
        })

        # UID of the client
        uid = api.get_uid(obj)

        # Bika Setup folder
        bika_setup = api.get_bika_setup()

        # bika samplepoints
        bika_samplepoints = bika_setup.bika_samplepoints
        bika_samplepoints_uid = api.get_uid(bika_samplepoints)

        # bika artemplates
        bika_artemplates = bika_setup.bika_artemplates
        bika_artemplates_uid = api.get_uid(bika_artemplates)

        # bika analysisprofiles
        bika_analysisprofiles = bika_setup.bika_analysisprofiles
        bika_analysisprofiles_uid = api.get_uid(bika_analysisprofiles)

        # bika analysisspecs
        bika_analysisspecs = bika_setup.bika_analysisspecs
        bika_analysisspecs_uid = api.get_uid(bika_analysisspecs)

        # catalog queries for UI field filtering
        filter_queries = {
            "contact": {
                "getParentUID": [uid]
            },
            "cc_contact": {
                "getParentUID": [uid]
            },
            "invoice_contact": {
                "getParentUID": [uid]
            },
            "samplepoint": {
                "getClientUID": [uid, bika_samplepoints_uid],
            },
            "artemplates": {
                "getClientUID": [uid, bika_artemplates_uid],
            },
            "analysisprofiles": {
                "getClientUID": [uid, bika_analysisprofiles_uid],
            },
            "analysisspecs": {
                "getClientUID": [uid, bika_analysisspecs_uid],
            },
            "samplinground": {
                "getParentUID": [uid],
            },
            "sample": {
                "getClientUID": [uid],
            },
        }
        info["filter_queries"] = filter_queries

        return info

    @cache(cache_key)
    def get_contact_info(self, obj):
        """Returns the client info of an object
        """

        info = self.get_base_info(obj)
        fullname = obj.getFullname()
        email = obj.getEmailAddress()

        # Note: It might get a circular dependency when calling:
        #       map(self.get_contact_info, obj.getCCContact())
        cccontacts = {}
        for contact in obj.getCCContact():
            uid = api.get_uid(contact)
            fullname = contact.getFullname()
            email = contact.getEmailAddress()
            cccontacts[uid] = {
                "fullname": fullname,
                "email": email
            }

        info.update({
            "fullname": fullname,
            "email": email,
            "cccontacts": cccontacts,
        })

        return info

    @cache(cache_key)
    def get_service_info(self, obj):
        """Returns the info for a Service
        """
        info = self.get_base_info(obj)

        info.update({
            "short_title": obj.getShortTitle(),
            "scientific_name": obj.getScientificName(),
            "unit": obj.getUnit(),
            "keyword": obj.getKeyword(),
            "methods": map(self.get_method_info, obj.getMethods()),
            "calculation": self.get_calculation_info(obj.getCalculation()),
            "price": obj.getPrice(),
            "currency_symbol": self.get_currency().symbol,
            "accredited": obj.getAccredited(),
            "category": obj.getCategoryTitle(),
            "poc": obj.getPointOfCapture(),

        })

        dependencies = get_calculation_dependencies_for(obj).values()
        info["dependencies"] = map(self.get_base_info, dependencies)
        return info

    @cache(cache_key)
    def get_template_info(self, obj):
        """Returns the info for a Template
        """
        client = self.get_client()
        client_uid = api.get_uid(client) if client else ""

        profile = obj.getAnalysisProfile()
        profile_uid = api.get_uid(profile) if profile else ""
        profile_title = profile.Title() if profile else ""

        sample_type = obj.getSampleType()
        sample_type_uid = api.get_uid(sample_type) if sample_type else ""
        sample_type_title = sample_type.Title() if sample_type else ""

        sample_point = obj.getSamplePoint()
        sample_point_uid = api.get_uid(sample_point) if sample_point else ""
        sample_point_title = sample_point.Title() if sample_point else ""

        service_uids = []
        analyses_partitions = {}
        analyses = obj.getAnalyses()

        for record in analyses:
            service_uid = record.get("service_uid")
            service_uids.append(service_uid)
            analyses_partitions[service_uid] = record.get("partition")

        info = self.get_base_info(obj)
        info.update({
            "analyses_partitions": analyses_partitions,
            "analysis_profile_title": profile_title,
            "analysis_profile_uid": profile_uid,
            "client_uid": client_uid,
            "composite": obj.getComposite(),
            "partitions": obj.getPartitions(),
            "remarks": obj.getRemarks(),
            "sample_point_title": sample_point_title,
            "sample_point_uid": sample_point_uid,
            "sample_type_title": sample_type_title,
            "sample_type_uid": sample_type_uid,
            "service_uids": service_uids,
        })
        return info

    @cache(cache_key)
    def get_profile_info(self, obj):
        """Returns the info for a Profile
        """
        info = self.get_base_info(obj)
        info.update({})
        return info

    @cache(cache_key)
    def get_method_info(self, obj):
        """Returns the info for a Method
        """
        info = self.get_base_info(obj)
        info.update({})
        return info

    @cache(cache_key)
    def get_calculation_info(self, obj):
        """Returns the info for a Calculation
        """
        info = self.get_base_info(obj)
        info.update({})
        return info

    @cache(cache_key)
    def get_sampletype_info(self, obj):
        """Returns the info for a Sample Type
        """
        info = self.get_base_info(obj)

        # Bika Setup folder
        bika_setup = api.get_bika_setup()

        # bika samplepoints
        bika_samplepoints = bika_setup.bika_samplepoints
        bika_samplepoints_uid = api.get_uid(bika_samplepoints)

        # bika analysisspecs
        bika_analysisspecs = bika_setup.bika_analysisspecs
        bika_analysisspecs_uid = api.get_uid(bika_analysisspecs)

        # client
        client = self.get_client()
        client_uid = client and api.get_uid(client) or ""

        # sample matrix
        sample_matrix = obj.getSampleMatrix()
        sample_matrix_uid = sample_matrix and sample_matrix.UID() or ""
        sample_matrix_title = sample_matrix and sample_matrix.Title() or ""

        # container type
        container_type = obj.getContainerType()
        container_type_uid = container_type and container_type.UID() or ""
        container_type_title = container_type and container_type.Title() or ""

        # sample points
        sample_points = obj.getSamplePoints()
        sample_point_uids = map(lambda sp: sp.UID(), sample_points)
        sample_point_titles = map(lambda sp: sp.Title(), sample_points)

        info.update({
            "prefix": obj.getPrefix(),
            "minimum_volume": obj.getMinimumVolume(),
            "hazardous": obj.getHazardous(),
            "retention_period": obj.getRetentionPeriod(),
            "sample_matrix_uid": sample_matrix_uid,
            "sample_matrix_title": sample_matrix_title,
            "container_type_uid": container_type_uid,
            "container_type_title": container_type_title,
            "sample_point_uids": sample_point_uids,
            "sample_point_titles": sample_point_titles,
        })

        # catalog queries for UI field filtering
        filter_queries = {
            "samplepoint": {
                "getSampleTypeTitles": [obj.Title(), ''],
                "getClientUID": [client_uid, bika_samplepoints_uid],
                "sort_order": "descending",
            },
            "specification": {
                "getSampleTypeTitle": obj.Title(),
                "getClientUID": [client_uid, bika_analysisspecs_uid],
                "sort_order": "descending",
            }
        }
        info["filter_queries"] = filter_queries

        return info

    @cache(cache_key)
    def get_sample_info(self, obj):
        """Returns the info for a Sample
        """
        info = self.get_base_info(obj)

        # sample type
        sample_type = obj.getSampleType()
        sample_type_uid = sample_type and sample_type.UID() or ""
        sample_type_title = sample_type and sample_type.Title() or ""

        # sample condition
        sample_condition = obj.getSampleCondition()
        sample_condition_uid = sample_condition \
            and sample_condition.UID() or ""
        sample_condition_title = sample_condition \
            and sample_condition.Title() or ""

        # storage location
        storage_location = obj.getStorageLocation()
        storage_location_uid = storage_location \
            and storage_location.UID() or ""
        storage_location_title = storage_location \
            and storage_location.Title() or ""

        # sample point
        sample_point = obj.getSamplePoint()
        sample_point_uid = sample_point and sample_point.UID() or ""
        sample_point_title = sample_point and sample_point.Title() or ""

        # container type
        container_type = sample_type and sample_type.getContainerType() or None
        container_type_uid = container_type and container_type.UID() or ""
        container_type_title = container_type and container_type.Title() or ""

        # Sampling deviation
        deviation = obj.getSamplingDeviation()
        deviation_uid = deviation and deviation.UID() or ""
        deviation_title = deviation and deviation.Title() or ""

        info.update({
            "sample_id": obj.getId(),
            "batch_uid": obj.getBatchUID() or None,
            "date_sampled": self.to_iso_date(obj.getDateSampled()),
            "sampling_date": self.to_iso_date(obj.getSamplingDate()),
            "sample_type_uid": sample_type_uid,
            "sample_type_title": sample_type_title,
            "container_type_uid": container_type_uid,
            "container_type_title": container_type_title,
            "sample_condition_uid": sample_condition_uid,
            "sample_condition_title": sample_condition_title,
            "storage_location_uid": storage_location_uid,
            "storage_location_title": storage_location_title,
            "sample_point_uid": sample_point_uid,
            "sample_point_title": sample_point_title,
            "environmental_conditions": obj.getEnvironmentalConditions(),
            "composite": obj.getComposite(),
            "client_uid": obj.getClientUID(),
            "client_title": obj.getClientTitle(),
            "contact": self.get_contact_info(obj.getContact()),
            "client_order_number": obj.getClientOrderNumber(),
            "client_sample_id": obj.getClientSampleID(),
            "client_reference": obj.getClientReference(),
            "sampling_deviation_uid": deviation_uid,
            "sampling_deviation_title": deviation_title,
            "sampling_workflow_enabled": obj.getSamplingWorkflowEnabled(),
            "remarks": obj.getRemarks(),
        })
        return info

    @cache(cache_key)
    def get_specification_info(self, obj):
        """Returns the info for a Specification
        """
        info = self.get_base_info(obj)

        results_range = obj.getResultsRange()
        info.update({
            "results_range": results_range,
            "sample_type_uid": obj.getSampleTypeUID(),
            "sample_type_title": obj.getSampleTypeTitle(),
            "client_uid": obj.getClientUID(),
        })

        bsc = api.get_tool("bika_setup_catalog")

        def get_service_by_keyword(keyword):
            if keyword is None:
                return []
            return map(api.get_object, bsc({
                "portal_type": "AnalysisService",
                "getKeyword": keyword
            }))

        # append a mapping of service_uid -> specification
        specifications = {}
        for spec in results_range:
            service_uid = spec.get("uid")
            if service_uid is None:
                # service spec is not attached to a specific service, but to a
                # keyword
                for service in get_service_by_keyword(spec.get("keyword")):
                    service_uid = api.get_uid(service)
                    specifications[service_uid] = spec
                continue
            specifications[service_uid] = spec
        info["specifications"] = specifications
        # spec'd service UIDs
        info["service_uids"] = specifications.keys()
        return info

    @cache(cache_key)
    def get_container_info(self, obj):
        """Returns the info for a Container
        """
        info = self.get_base_info(obj)
        info.update({})
        return info

    @cache(cache_key)
    def get_preservation_info(self, obj):
        """Returns the info for a Preservation
        """
        info = self.get_base_info(obj)
        info.update({})
        return info

    def ajax_get_global_settings(self):
        """Returns the global Bika settings
        """
        bika_setup = api.get_bika_setup()
        settings = {
            "show_prices": bika_setup.getShowPrices(),
        }
        return settings

    def ajax_get_service(self):
        """Returns the services information
        """
        uid = self.request.form.get("uid", None)

        if uid is None:
            return self.error("Invalid UID", status=400)

        service = self.get_object_by_uid(uid)
        if not service:
            return self.error("Service not found", status=404)

        info = self.get_service_info(service)
        return info

    def ajax_recalculate_records(self):
        """Recalculate all AR records and dependencies

            - samples
            - templates
            - profiles
            - services
            - dependecies

        XXX: This function has grown too much and needs refactoring!
        """
        out = {}

        # The sorted records from the request
        records = self.get_records()

        for n, record in enumerate(records):

            # Mapping of client UID -> client object info
            client_metadata = {}
            # Mapping of contact UID -> contact object info
            contact_metadata = {}
            # Mapping of sample UID -> sample object info
            sample_metadata = {}
            # Mapping of sampletype UID -> sampletype object info
            sampletype_metadata = {}
            # Mapping of specification UID -> specification object info
            specification_metadata = {}
            # Mapping of specification UID -> list of service UIDs
            specification_to_services = {}
            # Mapping of service UID -> list of specification UIDs
            service_to_specifications = {}
            # Mapping of template UID -> template object info
            template_metadata = {}
            # Mapping of template UID -> list of service UIDs
            template_to_services = {}
            # Mapping of service UID -> list of template UIDs
            service_to_templates = {}
            # Mapping of profile UID -> list of service UIDs
            profile_to_services = {}
            # Mapping of service UID -> list of profile UIDs
            service_to_profiles = {}
            # Profile metadata for UI purposes
            profile_metadata = {}
            # Mapping of service UID -> service object info
            service_metadata = {}
            # mapping of service UID -> unmet service dependency UIDs
            unmet_dependencies = {}

            # Mappings of UID -> object of selected items in this record
            _clients = self.get_objs_from_record(record, "Client_uid")
            _contacts = self.get_objs_from_record(record, "Contact_uid")
            _specifications = self.get_objs_from_record(
                record, "Specification_uid")
            _templates = self.get_objs_from_record(record, "Template_uid")
            _samples = self.get_objs_from_record(record, "PrimaryAnalysisRequest_uid")
            _profiles = self.get_objs_from_record(record, "Profiles_uid")
            _services = self.get_objs_from_record(record, "Analyses")
            _sampletypes = self.get_objs_from_record(record, "SampleType_uid")

            # CLIENTS
            for uid, obj in _clients.iteritems():
                # get the client metadata
                metadata = self.get_client_info(obj)
                # remember the sampletype metadata
                client_metadata[uid] = metadata

            # CONTACTS
            for uid, obj in _contacts.iteritems():
                # get the client metadata
                metadata = self.get_contact_info(obj)
                # remember the sampletype metadata
                contact_metadata[uid] = metadata

            # SPECIFICATIONS
            for uid, obj in _specifications.iteritems():
                # get the specification metadata
                metadata = self.get_specification_info(obj)
                # remember the metadata of this specification
                specification_metadata[uid] = metadata
                # get the spec'd service UIDs
                service_uids = metadata["service_uids"]
                # remember a mapping of specification uid -> spec'd services
                specification_to_services[uid] = service_uids
                # remember a mapping of service uid -> specifications
                for service_uid in service_uids:
                    if service_uid in service_to_specifications:
                        service_to_specifications[service_uid].append(uid)
                    else:
                        service_to_specifications[service_uid] = [uid]

            # AR TEMPLATES
            for uid, obj in _templates.iteritems():
                # get the template metadata
                metadata = self.get_template_info(obj)
                # remember the template metadata
                template_metadata[uid] = metadata

                # profile from the template
                profile = obj.getAnalysisProfile()
                # add the profile to the other profiles
                if profile is not None:
                    profile_uid = api.get_uid(profile)
                    _profiles[profile_uid] = profile

                # get the template analyses
                # [{'partition': 'part-1', 'service_uid': '...'},
                # {'partition': 'part-1', 'service_uid': '...'}]
                analyses = obj.getAnalyses() or []
                # get all UIDs of the template records
                service_uids = map(
                    lambda rec: rec.get("service_uid"), analyses)
                # remember a mapping of template uid -> service
                template_to_services[uid] = service_uids
                # remember a mapping of service uid -> templates
                for service_uid in service_uids:
                    # append service to services mapping
                    service = self.get_object_by_uid(service_uid)
                    # remember the template of all services
                    if service_uid in service_to_templates:
                        service_to_templates[service_uid].append(uid)
                    else:
                        service_to_templates[service_uid] = [uid]

                    # remember the service metadata
                    if service_uid not in service_metadata:
                        metadata = self.get_service_info(service)
                        service_metadata[service_uid] = metadata

            # PROFILES
            for uid, obj in _profiles.iteritems():
                # get the profile metadata
                metadata = self.get_profile_info(obj)
                # remember the profile metadata
                profile_metadata[uid] = metadata
                # get all services of this profile
                services = obj.getService()
                # get all UIDs of the profile services
                service_uids = map(api.get_uid, services)
                # remember all services of this profile
                profile_to_services[uid] = service_uids
                # remember a mapping of service uid -> profiles
                for service in services:
                    # get the UID of this service
                    service_uid = api.get_uid(service)
                    # add the service to the other services
                    _services[service_uid] = service
                    # remember the profiles of this service
                    if service_uid in service_to_profiles:
                        service_to_profiles[service_uid].append(uid)
                    else:
                        service_to_profiles[service_uid] = [uid]

            # PRIMARY ANALYSIS REQUESTS
            for uid, obj in _samples.iteritems():
                # get the sample metadata
                metadata = self.get_sample_info(obj)
                # remember the sample metadata
                sample_metadata[uid] = metadata

            # SAMPLETYPES
            for uid, obj in _sampletypes.iteritems():
                # get the sampletype metadata
                metadata = self.get_sampletype_info(obj)
                # remember the sampletype metadata
                sampletype_metadata[uid] = metadata

            # SERVICES
            for uid, obj in _services.iteritems():
                # get the service metadata
                metadata = self.get_service_info(obj)

                # remember the services' metadata
                service_metadata[uid] = metadata

            #  DEPENDENCIES
            for uid, obj in _services.iteritems():
                # get the dependencies of this service
                deps = get_service_dependencies_for(obj)

                # check for unmet dependencies
                for dep in deps["dependencies"]:
                    # we use the UID to test for equality
                    dep_uid = api.get_uid(dep)
                    if dep_uid not in _services.keys():
                        if uid in unmet_dependencies:
                            unmet_dependencies[uid].append(
                                self.get_base_info(dep))
                        else:
                            unmet_dependencies[uid] = [self.get_base_info(dep)]
                # remember the dependencies in the service metadata
                service_metadata[uid].update({
                    "dependencies": map(
                        self.get_base_info, deps["dependencies"]),
                })

            # Each key `n` (1,2,3...) contains the form data for one AR Add
            # column in the UI.
            # All relevant form data will be set accoriding to this data.
            out[n] = {
                "client_metadata": client_metadata,
                "contact_metadata": contact_metadata,
                "sample_metadata": sample_metadata,
                "sampletype_metadata": sampletype_metadata,
                "specification_metadata": specification_metadata,
                "specification_to_services": specification_to_services,
                "service_to_specifications": service_to_specifications,
                "template_metadata": template_metadata,
                "template_to_services": template_to_services,
                "service_to_templates": service_to_templates,
                "profile_metadata": profile_metadata,
                "profile_to_services": profile_to_services,
                "service_to_profiles": service_to_profiles,
                "service_metadata": service_metadata,
                "unmet_dependencies": unmet_dependencies,
            }

        return out

    def show_recalculate_prices(self):
        bika_setup = api.get_bika_setup()
        return bika_setup.getShowPrices()

    def ajax_recalculate_prices(self):
        """Recalculate prices for all ARs
        """
        # When the option "Include and display pricing information" in
        # Bika Setup Accounting tab is not selected
        if not self.show_recalculate_prices():
            return {}

        # The sorted records from the request
        records = self.get_records()

        client = self.get_client()
        bika_setup = api.get_bika_setup()

        member_discount = float(bika_setup.getMemberDiscount())
        member_discount_applies = False
        if client:
            member_discount_applies = client.getMemberDiscountApplies()

        prices = {}
        for n, record in enumerate(records):
            ardiscount_amount = 0.00
            arservices_price = 0.00
            arprofiles_price = 0.00
            arprofiles_vat_amount = 0.00
            arservice_vat_amount = 0.00
            services_from_priced_profile = []

            profile_uids = record.get("Profiles_uid", "").split(",")
            profile_uids = filter(lambda x: x, profile_uids)
            profiles = map(self.get_object_by_uid, profile_uids)
            services = map(self.get_object_by_uid, record.get("Analyses", []))

            # ANALYSIS PROFILES PRICE
            for profile in profiles:
                use_profile_price = profile.getUseAnalysisProfilePrice()
                if not use_profile_price:
                    continue

                profile_price = float(profile.getAnalysisProfilePrice())
                arprofiles_price += profile_price
                arprofiles_vat_amount += profile.getVATAmount()
                profile_services = profile.getService()
                services_from_priced_profile.extend(profile_services)

            # ANALYSIS SERVICES PRICE
            for service in services:
                if service in services_from_priced_profile:
                    continue
                service_price = float(service.getPrice())
                # service_vat = float(service.getVAT())
                service_vat_amount = float(service.getVATAmount())
                arservice_vat_amount += service_vat_amount
                arservices_price += service_price

            base_price = arservices_price + arprofiles_price

            # Calculate the member discount if it applies
            if member_discount and member_discount_applies:
                logger.info("Member discount applies with {}%".format(
                    member_discount))
                ardiscount_amount = base_price * member_discount / 100

            subtotal = base_price - ardiscount_amount
            vat_amount = arprofiles_vat_amount + arservice_vat_amount
            total = subtotal + vat_amount

            prices[n] = {
                "discount": "{0:.2f}".format(ardiscount_amount),
                "subtotal": "{0:.2f}".format(subtotal),
                "vat": "{0:.2f}".format(vat_amount),
                "total": "{0:.2f}".format(total),
            }
            logger.info("Prices for AR {}: Discount={discount} "
                        "VAT={vat} Subtotal={subtotal} total={total}"
                        .format(n, **prices[n]))

        return prices

    def ajax_submit(self):
        """Submit & create the ARs
        """

        # Get AR required fields (including extended fields)
        fields = self.get_ar_fields()

        # extract records from request
        records = self.get_records()

        fielderrors = {}
        errors = {"message": "", "fielderrors": {}}

        attachments = {}
        valid_records = []

        # Validate required fields
        for n, record in enumerate(records):

            # Process UID fields first and set their values to the linked field
            uid_fields = filter(lambda f: f.endswith("_uid"), record)
            for field in uid_fields:
                name = field.replace("_uid", "")
                value = record.get(field)
                if "," in value:
                    value = value.split(",")
                record[name] = value

            # Extract file uploads (fields ending with _file)
            # These files will be added later as attachments
            file_fields = filter(lambda f: f.endswith("_file"), record)
            attachments[n] = map(lambda f: record.pop(f), file_fields)

            # Process Specifications field (dictionary like records instance).
            # -> Convert to a standard Python dictionary.
            specifications = map(
                lambda x: dict(x), record.pop("Specifications", []))
            record["Specifications"] = specifications

            # Required fields and their values
            required_keys = [field.getName() for field in fields
                             if field.required]
            required_values = [record.get(key) for key in required_keys]
            required_fields = dict(zip(required_keys, required_values))

            # Client field is required but hidden in the AR Add form. We remove
            # it therefore from the list of required fields to let empty
            # columns pass the required check below.
            if record.get("Client", False):
                required_fields.pop('Client', None)

            # Contacts get pre-filled out if only one contact exists.
            # We won't force those columns with only the Contact filled out to
            # be required.
            contact = required_fields.pop("Contact", None)

            # None of the required fields are filled, skip this record
            if not any(required_fields.values()):
                continue

            # Re-add the Contact
            required_fields["Contact"] = contact

            # Missing required fields
            missing = [f for f in required_fields if not record.get(f, None)]

            # If there are required fields missing, flag an error
            for field in missing:
                fieldname = "{}-{}".format(field, n)
                msg = _("Field '{}' is required".format(field))
                fielderrors[fieldname] = msg

            # Process valid record
            valid_record = dict()
            for fieldname, fieldvalue in record.iteritems():
                # clean empty
                if fieldvalue in ['', None]:
                    continue
                valid_record[fieldname] = fieldvalue

            # append the valid record to the list of valid records
            valid_records.append(valid_record)

        # return immediately with an error response if some field checks failed
        if fielderrors:
            errors["fielderrors"] = fielderrors
            return {'errors': errors}

        # Process Form
        actions = ActionHandlerPool.get_instance()
        actions.queue_pool()
        ARs = OrderedDict()
        for n, record in enumerate(valid_records):
            client_uid = record.get("Client")
            client = self.get_object_by_uid(client_uid)

            if not client:
                actions.resume()
                raise RuntimeError("No client found")

            # get the specifications and pass them to the AR create function.
            specifications = record.pop("Specifications", {})

            # Create the Analysis Request
            try:
                ar = crar(
                    client,
                    self.request,
                    record,
                    specifications=specifications
                )
            except (KeyError, RuntimeError) as e:
                actions.resume()
                errors["message"] = e.message
                return {"errors": errors}
            # We keep the title to check if AR is newly created
            # and UID to print stickers
            ARs[ar.Title()] = ar.UID()
            for attachment in attachments.get(n, []):
                if not attachment.filename:
                    continue
                att = _createObjectByType("Attachment", client, tmpID())
                att.setAttachmentFile(attachment)
                att.processForm()
                ar.addAttachment(att)
        actions.resume()

        level = "info"
        if len(ARs) == 0:
            message = _('No Samples could be created.')
            level = "error"
        elif len(ARs) > 1:
            message = _('Samples ${ARs} were successfully created.',
                        mapping={'ARs': safe_unicode(', '.join(ARs.keys()))})
        else:
            message = _('Sample ${AR} was successfully created.',
                        mapping={'AR': safe_unicode(ARs.keys()[0])})

        # Display a portal message
        self.context.plone_utils.addPortalMessage(message, level)

        # Automatic label printing
        bika_setup = api.get_bika_setup()
        auto_print = bika_setup.getAutoPrintStickers()
        if 'register' in auto_print and ARs:
            return {
                'success': message,
                'stickers': ARs.values(),
                'stickertemplate': bika_setup.getAutoStickerTemplate()
            }
        else:
            return {'success': message}
