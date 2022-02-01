"""
Interface to obtain objects from skyCatalogs.
"""
import math
import numpy as np
from galsim.config import InputLoader, RegisterInputType, RegisterValueType, \
    RegisterObjectType, RegisterSEDType
from galsim import CelestialCoord
import galsim
from desc.skycatalogs import skyCatalogs
from .instcat import get_radec_limits


class SkyCatalogInterface:
    """Interface to skyCatalogs package."""
    _bp500 = galsim.Bandpass(galsim.LookupTable([499,500,501],[0,1,0]),
                             wave_type='nm').withZeropoint('AB')

    # Using area-weighted effective aperture over FOV
    # from https://confluence.lsstcorp.org/display/LKB/LSST+Key+Numbers
    _rubin_area = 0.25 * np.pi * 649**2  # cm^2

    def __init__(self, file_name, wcs, obj_types=('galaxy',),
                 edge_pix=100, flip_g2=True, logger=None):
        logger = galsim.config.LoggerWrapper(logger)
        self.file_name = file_name
        self.wcs = wcs
        self.flip_g2 = flip_g2
        sky_cat = skyCatalogs.open_catalog(file_name)
        region = skyCatalogs.Box(*get_radec_limits(wcs, logger, edge_pix)[:4])
        obj_type_set = set(obj_types)
        self.objects = sky_cat.get_objects_by_region(region,
                                                     obj_type_set=obj_type_set)
        self._index_subcomponents()

    def _index_subcomponents(self):
        self._index = []
        for i, obj in enumerate(self.objects):
            subcomponents = obj.subcomponents
            if not subcomponents:
                subcomponents = [None]
            for component in subcomponents:
                self._index.append((i, component))

    def get_skycat_obj(self, index):
        """
        Return the skyCatalog object corresponding to the specified index.

        Parameters
        ----------
        index : int
            Index of the (object_index, subcomponent) combination.

        Returns
        -------
        desc.skycatalogs.BaseObject
        """
        obj_index, _ = self._index[index]
        return self.objects[obj_index]

    def getNObjects(self):
        """
        Return the number of GSObjects to render, where each subcomponent
        (e.g., bulge, disk, etc.) of each skyCatalog object is a distinct
        GSObject.
        """
        return len(self._index)

    def getSED_info(self, index):
        """
        Return the SED and magnorm value of the skyCatalog object
        corresponding to the specified index.

        Parameters
        ----------
        index : int
            Index of the (object_index, subcomponent) combination.

        Returns
        -------
        (galsim.SED, float)
        """
        obj_index, component = self._index[index]
        wl, flambda, magnorm \
            = self.objects[obj_index].get_sed(component=component)
        if np.isinf(magnorm):
            return None, magnorm
        sed_lut = galsim.LookupTable(wl, flambda)
        sed = galsim.SED(sed_lut, wave_type='nm', flux_type='flambda')
        sed = sed.withMagnitude(0, self._bp500)
        return sed, magnorm

    def getWorldPos(self, index):
        """
        Return the sky coordinates of the skyCatalog object
        corresponding to the specified index.

        Parameters
        ----------
        index : int
            Index of the (object_index, subcomponent) combination.

        Returns
        -------
        galsim.CelestialCoord
        """
        skycat_obj = self.get_skycat_obj(index)
        ra, dec = skycat_obj.ra, skycat_obj.dec
        return galsim.CelestialCoord(ra*galsim.degrees, dec*galsim.degrees)

    def getLens(self, index):
        """
        Return the weak lensing parameters for the skyCatalog object
        corresponding to the specified index.

        Parameters
        ----------
        index : int
            Index of the (object_index, subcomponent) combination.

        Returns
        -------
        (g1, g2, mu)
        """
        skycat_obj = self.get_skycat_obj(index)
        gamma1 = skycat_obj.get_native_attribute('shear_1')
        gamma2 = skycat_obj.get_native_attribute('shear_2')
        kappa =  skycat_obj.get_native_attribute('convergence')
        # Return reduced shears and magnification.
        g1 = gamma1/(1. - kappa)    # real part of reduced shear
        g2 = gamma2/(1. - kappa)    # imaginary part of reduced shear
        mu = 1./((1. - kappa)**2 - (gamma1**2 + gamma2**2)) # magnification
        return g1, g2, mu

    def getDust(self, index, band='i'):
        """
        Return the extinction parameters for the skyCatalog object
        corresponding to the specified index.

        Parameters
        ----------
        index : int
            Index of the (object_index, subcomponent) combination.

        Returns
        -------
        (internal_av, internal_rv, galactic_av, galactic_rv)
        """
        skycat_obj = self.get_skycat_obj(index)
        # For all objects, internal extinction is already part of SED,
        # so Milky Way dust is the only source of reddening.
        internal_av = 0
        internal_rv = 1.
        MW_av_colname = f'MW_av_lsst_{band}'
        galactic_av = skycat_obj.get_native_attribute(MW_av_colname)
        galactic_rv = skycat_obj.get_native_attribute('MW_rv')
        return internal_av, internal_rv, galactic_av, galactic_rv

    def getObj(self, index, gsparams=None, rng=None, bandpass=None,
               chromatic=False, exp_time=30):
        """
        Return the galsim object for the skyCatalog object
        corresponding to the specified index.

        Parameters
        ----------
        index : int
            Index of the (object_index, subcomponent) combination.

        Returns
        -------
        galsim.GSObject
        """
        obj_index, component = self._index[index]
        skycat_obj = self.objects[obj_index]
        sed, magnorm = self.getSED_info(index)
        if sed is None or magnorm >= 50:
            return None

        if gsparams is not None:
            gsparams = galsim.GSParams(**gsparams)

        if skycat_obj.object_type == 'star':
            obj = galsim.DeltaFunction(gsparams=gsparams)
        elif (skycat_obj.object_type == 'galaxy' and
              component in ('bulge', 'disk', 'knots')):
            my_component = component
            if my_component == 'knots':
                my_component = 'disk'
            a = skycat_obj.get_native_attribute(f'size_{my_component}_true')
            b = skycat_obj.get_native_attribute(f'size_minor_{my_component}_true')
            assert a >= b
            pa = skycat_obj.get_native_attribute('position_angle_unlensed')
            if self.flip_g2:
                beta = float(90 - pa)*galsim.degrees
            else:
                beta = float(90 + pa)*galsim.degrees
            # TODO: check if should be hlr = a. See similar note in instcat.py.
            hlr = (a*b)**0.5   # approximation for half-light radius
            if component == 'knots':
                npoints = skycat_obj.get_native_attribute(f'n_knots')
                assert npoints > 0
                obj =  galsim.RandomKnots(npoints=npoints,
                                          half_light_radius=hlr, rng=rng,
                                          gsparams=gsparams)
            else:
                n = skycat_obj.get_native_attribute(f'sersic_{component}')
                # Quantize the n values at 0.05 so that galsim can
                # possibly amortize sersic calculations from the previous
                # galaxy.
                n = round(n*20.)/20.
                obj = galsim.Sersic(n=n, half_light_radius=hlr,
                                    gsparams=gsparams)
            shear = galsim.Shear(q=b/a, beta=beta)
            obj = obj._shear(shear)
            g1, g2, mu = self.getLens(index)
            obj = obj._lens(g1, g2, mu)
        else:
            raise RuntimeError("Do not know how to handle object type: %s" %
                               component)

        # The seds are normalized to correspond to magnorm=0.
        # The flux for the given magnorm is 10**(-0.4*magnorm)
        # The constant here, 0.9210340371976184 = 0.4 * log(10)
        flux = math.exp(-0.9210340371976184 * magnorm)

        # This gives the normalization in photons/cm^2/sec.
        # Multiply by area and exptime to get photons.
        fAt = flux * self._rubin_area * exp_time
        if chromatic:
            return obj.withFlux(fAt) * sed

        flux = sed.calculateFlux(bandpass) * fAt
        return obj.withFlux(flux)


class SkyCatalogLoader(InputLoader):
    """
    Class to load SkyCatalogInterface object.
    """
    def getKwargs(self, config, base, logger):
        req = {'file_name': str}
        opt = {
               'edge_pix' : float,
               'flip_g2' : bool,
              }
        kwargs, safe = galsim.config.GetAllParams(config, base, req=req,
                                                  opt=opt)
        wcs = galsim.config.BuildWCS(base['image'], 'wcs', base, logger=logger)
        kwargs['wcs'] = wcs
        kwargs['logger'] = galsim.config.GetLoggerProxy(logger)
        return kwargs, safe


def SkyCatObj(config, base, ignore, gsparams, logger):
    """
    Build an object according to info in the sky catalog.
    """
    skycat = galsim.config.GetInputObj('sky_catalog', config, base, 'SkyCatObj')

    # Setup the indexing sequence if it hasn't been specified.  The
    # normal thing with a catalog is to just use each object in order,
    # so we don't require the user to specify that by hand.  We can do
    # it for them.
    galsim.config.SetDefaultIndex(config, skycat.getNObjects())

    req = { 'index' : int }
    opt = { 'num' : int }
    kwargs, safe = galsim.config.GetAllParams(config, base, req=req, opt=opt)
    index = kwargs['index']

    rng = galsim.config.GetRNG(config, base, logger, 'SkyCatObj')
    bp = base['bandpass']
    exp_time = base.get('exp_time', None)

    obj = skycat.getObj(index, gsparams=gsparams, rng=rng, bandpass=bp,
                        exp_time=exp_time)

    return obj, safe


def SkyCatWorldPos(config, base, value_type):
    """Return a value from the object part of the skyCatalog
    """
    skycat = galsim.config.GetInputObj('sky_catalog', config, base,
                                       'SkyCatWorldPos')

    # Setup the indexing sequence if it hasn't been specified.  The
    # normal thing with a catalog is to just use each object in order,
    # so we don't require the user to specify that by hand.  We can do
    # it for them.
    galsim.config.SetDefaultIndex(config, skycat.getNObjects())

    req = { 'index' : int }
    opt = { 'num' : int }
    kwargs, safe = galsim.config.GetAllParams(config, base, req=req, opt=opt)
    index = kwargs['index']

    pos = skycat.getWorldPos(index)
    return pos, safe


class SkyCatSEDBuilder(galsim.config.SEDBuilder):
    """A class for loading an SED from the sky catalog.
    """
    def buildSED(self, config, base, logger):
        """Build the SED based on the specifications in the config dict.

        Parameters:
            config:     The configuration dict for the SED type.
            base:       The base configuration dict.
            logger:     If provided, a logger for logging debug statements.

        Returns:
            the constructed SED object.
        """
        skycat = galsim.config.GetInputObj('sky_catalog', config, base,
                                           'SkyCatSEDBuilder')

        # Setup the indexing sequence if it hasn't been specified.  The
        # normal thing with a catalog is to just use each object in order,
        # so we don't require the user to specify that by hand.  We can do
        # it for them.
        galsim.config.SetDefaultIndex(config, skycat.getNObjects())

        req = { 'index' : int }
        opt = { 'num' : int }
        kwargs, safe = galsim.config.GetAllParams(config, base, req=req,
                                                  opt=opt)
        index = kwargs['index']
        sed, _ = skycat.getSED_info(index)
        return sed, safe


RegisterInputType('sky_catalog',
                  SkyCatalogLoader(SkyCatalogInterface, has_nobj=True))
RegisterObjectType('SkyCatObj', SkyCatObj, input_type='sky_catalog')
RegisterValueType('SkyCatWorldPos', SkyCatWorldPos, [CelestialCoord],
                  input_type='sky_catalog')
RegisterSEDType('SkyCatSED', SkyCatSEDBuilder(), input_type='sky_catalog')
