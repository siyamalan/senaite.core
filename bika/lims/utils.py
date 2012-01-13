from AccessControl import ModuleSecurityInfo, allow_module
from DateTime import DateTime
from Products.CMFCore.utils import getToolByName
from Products.CMFPlone.TranslationServiceTool import TranslationServiceTool
from Products.Five.browser import BrowserView
from bika.lims import bikaMessageFactory as _
from bika.lims import interfaces
from bika.lims import logger
from bika.lims.config import Publish
from email.Utils import formataddr
from plone.i18n.normalizer.interfaces import IIDNormalizer
from reportlab.graphics.barcode import getCodes, getCodeNames, createBarcodeDrawing
from zope.component import getUtility
from zope.interface import providedBy
import copy,re,urllib
import plone.protect
import transaction

ModuleSecurityInfo('email.Utils').declarePublic('formataddr')
allow_module('csv')

# Wrapper for PortalTransport's sendmail - don't know why there sendmail
# method is marked private
ModuleSecurityInfo('Products.bika.utils').declarePublic('sendmail')
#Protected( Publish, 'sendmail')
def sendmail(portal, from_addr, to_addrs, msg):
    mailspool = portal.portal_mailspool
    mailspool.sendmail(from_addr, to_addrs, msg)

ModuleSecurityInfo('Products.bika.utils').declarePublic('printfile')
def printfile(portal, from_addr, to_addrs, msg):
    import os

    """ set the path, then the cmd 'lpr filepath'
    temp_path = 'C:/Zope2/Products/Bika/version.txt'

    os.system('lpr "%s"' %temp_path)
    """
    pass

def getAnalysts(context):
    """ Present the LabManagers and Analysts as options for analyst
    """
    mtool = getToolByName(context, 'portal_membership')
    pairs = []
    analysts = mtool.searchForMembers(roles = ['Manager', 'LabManager', 'Analyst'])
    for member in analysts:
        uid = member.getId()
        fullname = member.getProperty('fullname')
        if fullname is None:
            fullname = uid
        pairs.append((uid, fullname))
    pairs.sort(lambda x, y: cmp(x[1], y[1]))
    return pairs

def isActive(obj):
    """ Check if obj is inactive or cancelled.
    """
    wf = getToolByName(obj, 'portal_workflow')
    if (hasattr(obj, 'inactive_state') and obj.inactive_state == 'inactive') or \
       wf.getInfoFor(obj, 'inactive_state', 'active') == 'inactive':
        return False
    if (hasattr(obj, 'cancellation_state') and obj.inactive_state == 'cancelled') or \
       wf.getInfoFor(obj, 'cancellation_state', 'active') == 'cancelled':
        return False
    return True

def TimeOrDate(context, datetime, long_format = False):
    """ Return the Time date is today,
        otherwise return the Date.
        XXX timeordate needs long/short/time/date formats in bika_setup
"""
    localLongTimeFormat = context.portal_properties.site_properties.localLongTimeFormat
    localTimeFormat = context.portal_properties.site_properties.localTimeFormat
    localTimeOnlyFormat = context.portal_properties.site_properties.localTimeOnlyFormat

    if hasattr(datetime, 'Date'):
        if (datetime.Date() > DateTime().Date()) or long_format:
            dt = datetime.asdatetime().strftime(localLongTimeFormat)
        elif (datetime.Date() < DateTime().Date()):
            dt = datetime.asdatetime().strftime("%d %b %Y")
        elif datetime.Date() == DateTime().Date():
            dt = datetime.asdatetime().strftime(localTimeOnlyFormat)
        else:
            dt = datetime.asdatetime().strftime(localTimeFormat)
        dt = dt.replace("PM", "pm").replace("AM", "am")
        if len(dt) > 10:
            dt = dt.replace("12:00 am", "")
        if dt == "12:00 am":
            dt = datetime.asdatetime().strftime(localTimeFormat)
    else:
        dt = datetime
    return dt

class ajaxGetObject(BrowserView):
    """ return redirect url if the item exists
        passes the request to portal_catalog
        requires '_authenticator' in request.
    """
    def __call__(self):
        try:
            plone.protect.CheckAuthenticator(self.request)
            plone.protect.PostOnly(self.request)
        except:
            return ""
        pc = getToolByName(self.context, 'portal_catalog')
        id = self.request.get("id", '').replace("*", "")
        items = pc(self.request)
        if items:
            return items[0].getObject().absolute_url()

# encode_header function copied from roundup's rfc2822 package.
hqre = re.compile(r'^[A-z0-9!"#$%%&\'()*+,-./:;<=>?@\[\]^_`{|}~ ]+$')

ModuleSecurityInfo('Products.bika.utils').declarePublic('encode_header')
def encode_header(header, charset = 'utf-8'):
    """ Will encode in quoted-printable encoding only if header
    contains non latin characters
    """

    # Return empty headers unchanged
    if not header:
        return header

    # return plain header if it does not contain non-ascii characters
    if hqre.match(header):
        return header

    quoted = ''
    #max_encoded = 76 - len(charset) - 7
    for c in header:
        # Space may be represented as _ instead of =20 for readability
        if c == ' ':
            quoted += '_'
        # These characters can be included verbatim
        elif hqre.match(c):
            quoted += c
        # Otherwise, replace with hex value like =E2
        else:
            quoted += "=%02X" % ord(c)
            plain = 0

    return '=?%s?q?%s?=' % (charset, quoted)


def zero_fill(matchobj):
    return matchobj.group().zfill(8)

num_sort_regex = re.compile('\d+')

ModuleSecurityInfo('Products.bika.utils').declarePublic('sortable_title')
def sortable_title(portal, title):
    """Convert title to sortable title
    """
    if not title:
        return ''

    def_charset = portal.plone_utils.getSiteEncoding()
    sortabletitle = title.lower().strip()
    # Replace numbers with zero filled numbers
    sortabletitle = num_sort_regex.sub(zero_fill, sortabletitle)
    # Truncate to prevent bloat
    for charset in [def_charset, 'latin-1', 'utf-8']:
        try:
            sortabletitle = unicode(sortabletitle, charset)[:30]
            sortabletitle = sortabletitle.encode(def_charset or 'utf-8')
            break
        except UnicodeError:
            pass
        except TypeError:
            # If we get a TypeError if we already have a unicode string
            sortabletitle = sortabletitle[:30]
            break
    return sortabletitle

class IDServerUnavailable(Exception):
    pass

def idserver_generate_id(context, prefix, batch_size = None):
    """ Generate a new id using external ID server.
    """
    plone = context.portal_url.getPortalObject()
    portal_id = plone.getId()

    url = context.bika_setup.getIDServerURL()

    try:
        if batch_size:
            # GET
            f = urllib.urlopen('%s/%s/%s?%s' % (
                    url,
                    portal_id,
                    prefix,
                    urllib.urlencode({'batch_size': batch_size}))
                    )
        else:
            f = urllib.urlopen('%s/%s/%s' % (
                url, portal_id, prefix
                )
            )
        new_id = f.read()
        f.close()
    except:
        from sys import exc_info
        info = exc_info()
        import zLOG; zLOG.LOG('INFO', 0, '', 'generate_id raised exception: %s, %s \n ID server URL: %s' % (info[0], info[1], url))
        raise IDServerUnavailable(_('ID Server unavailable'))

    return new_id

def generateUniqueId(context):
    """ Generate pretty content IDs.
        - context is used to find portal_type; in case there is no
          prefix specified for the type, the normalized portal_type is
          used as a prefix instead.
    """

    norm = getUtility(IIDNormalizer).normalize
    prefixes = context.bika_setup.getPrefixes()

    year = context.bika_setup.getYearInPrefix() and \
        DateTime().strftime("%Y")[2:] or ''


    # Special case for Analysis Request IDs to be based on sample
    if context.portal_type == "AnalysisRequest":
        s_prefix = context.getSample().getSampleType().getPrefix()
        s_padding = context.bika_setup.getSampleIDPadding()
        ar_padding = context.bika_setup.getARIDPadding()
        sample = context.getSample()
        sample_id = sample.getId()
        s_number = sample_id.split(s_prefix)[1]
        ar_number = sample.getLastARNumber()
        ar_number = ar_number and ar_number + 1 or 1
        sample.setLastARNumber(ar_number)
        return "%s%s-%s" % (s_prefix,
                           str(s_number).zfill(s_padding),
                           str(ar_number).zfill(ar_padding))

    if context.bika_setup.getExternalIDServer():

        # if using external server
        # ========================
        for d in prefixes:
            # Sample ID comes from SampleType
            if context.portal_type == "Sample":
                prefix = context.getSampleType().getPrefix()
                padding = context.bika_setup.getSampleIDPadding()
                new_id = str(idserver_generate_id(context,
                                                  "%s%s-" % (prefix, year)))
                if padding:
                    new_id = new_id.zfill(int(padding))
                return '%s%s-%s' % (prefix, year, new_id)
            elif d['portal_type'] == context.portal_type:
                prefix = d['prefix']
                padding = d['padding']
                new_id = str(idserver_generate_id(context,
                                                  "%s%s-" % (prefix, year)))
                if padding:
                    new_id = new_id.zfill(int(padding))
                return '%s%s-%s' % (prefix, year, new_id)
        # no prefix; use portal_type
        # year is not inserted here
        new_id = str(idserver_generate_id(context, norm(context.portal_type) + "-"))
        return '%s-%s' % (norm(context.portal_type), new_id)

    else:

        # No external id-server.
        # ======================
        def next_id(prefix):

            # grab the first catalog we are indexed in.
            at = getToolByName(context, 'archetype_tool')
            plone = context.portal_url.getPortalObject()
            catalog_name = context.portal_type in at.catalog_map \
                and at.catalog_map[context.portal_type][0] or 'portal_catalog'
            catalog = getToolByName(plone, catalog_name)

            # get all IDS that start with prefix
            # this must specifically exclude AR IDs (two -'s)
            r = re.compile("^"+prefix+"-[\d]+$")
            ids = [int(i.split(prefix+"-")[1]) \
                   for i in catalog.Indexes['id'].uniqueValues() \
                   if r.match(i)]
            #plone_tool = getToolByName(context, 'plone_utils')
            #if not plone_tool.isIDAutoGenerated(l.id):
            ids.sort()
            _id = ids and ids[-1] or 0
            new_id = _id + 1
            return str(new_id)

        for d in prefixes:
            if context.portal_type == "Sample":
                # Special case for Sample IDs
                prefix = context.getSampleType().getPrefix()
                padding = context.bika_setup.getSampleIDPadding()
                new_id = next_id(prefix+year)
                if padding:
                    new_id = new_id.zfill(int(padding))
                return '%s%s-%s' % (prefix, year, new_id)
            elif d['portal_type'] == context.portal_type:
                prefix = d['prefix']
                padding = d['padding']
                new_id = next_id(prefix+year)
                if padding:
                    new_id = new_id.zfill(int(padding))
                return '%s%s-%s' % (prefix, year, new_id)

        # no prefix; use portal_type
        # no year inserted here
        prefix = norm(context.portal_type);
        new_id = next_id(prefix)
        return '%s-%s' % (prefix, new_id)

def renameAfterCreation(obj):
    # Can't rename without a subtransaction commit when using portal_factory
    transaction.savepoint(optimistic=True)
    new_id = generateUniqueId(obj)
    obj.aq_inner.aq_parent.manage_renameObject(obj.id, new_id)
