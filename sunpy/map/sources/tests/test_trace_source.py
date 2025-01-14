import os

import pytest

import astropy.units as u

import sunpy.data.test
from sunpy.map import Map
from sunpy.map.sources.trace import TRACEMap
from sunpy.util.exceptions import SunpyMetadataWarning

path = sunpy.data.test.rootdir
fitspath = os.path.join(path, "tsi20010130_025823_a2.fits")


@pytest.fixture(scope="module")
def createTRACE():
    """Creates a TRACEMap from a FITS file."""
    return Map(fitspath)


# TRACE Tests
def test_fitstoTRACE(createTRACE):
    """Tests the creation of TRACEMap using FITS."""
    assert isinstance(createTRACE, TRACEMap)


def test_is_datasource_for(createTRACE):
    """Test the is_datasource_for method of TRACEMap.
    Note that header data to be provided as an argument
    can be a MetaDict object."""
    assert createTRACE.is_datasource_for(createTRACE.data, createTRACE.meta)


def test_measurement(createTRACE):
    """Tests the measurement property of the TRACEMap object."""
    assert int(createTRACE.measurement) == 171


def test_observatory(createTRACE):
    """Tests the observatory property of the TRACEMap object."""
    assert createTRACE.observatory == "TRACE"


def test_norm_clip(createTRACE):
    # Tests that the default normalizer has clipping disabled
    assert not createTRACE.plot_settings['norm'].clip


def test_wcs(createTRACE):
    # Smoke test that WCS is valid and can transform from pixels to world coordinates
    with pytest.warns(SunpyMetadataWarning, match='Missing metadata for observer'):
        createTRACE.pixel_to_world(0*u.pix, 0*u.pix)
