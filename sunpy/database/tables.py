# Author: Simon Liedtke <liedtke.simon@googlemail.com>
#
# This module was developed with funding provided by
# the Google Summer of Code (2013).

from __future__ import absolute_import

from time import strptime, mktime
from datetime import datetime
import fnmatch
import os
from itertools import imap

from astropy.units import Unit, nm, equivalencies
from sqlalchemy import Column, Integer, Float, String, DateTime, Boolean,\
    Table, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.ext.declarative import declarative_base

from sunpy.time import parse_time
from sunpy.io import fits, file_tools as sunpy_filetools
from sunpy.util import print_table

__all__ = ['FitsHeaderEntry', 'Tag', 'DatabaseEntry', 'display_entries']

Base = declarative_base()

# required for the many-to-many relation on tags:entries
association_table = Table('association', Base.metadata,
    Column('tag_name', String, ForeignKey('tags.name')),
    Column('entry_id', Integer, ForeignKey('data.id'))
)


class WaveunitNotFoundError(Exception):
    """This exception is raised if a wavelength unit cannot be found in a FITS
    header or in a VSO query result block.

    """
    def __init__(self, obj):
        self.obj = obj

    def __str__(self):  # pragma: no cover
        return 'the wavelength unit cannot be found in %s' % (self.obj,)


class WaveunitNotConvertibleError(Exception):
    """This exception is raised if a wavelength cannot be converted to an
    astropy.units.Unit instance.

    """
    def __init__(self, waveunit):
        self.waveunit = waveunit

    def __str__(self):  # pragma: no cover
        return (
            'the waveunit %r cannot be converted to an '
            'astropy.units.Unit instance' % (self.waveunit))


# TODO: move this function outside this package (sunpy.util? sunpy.time?)
def timestamp2datetime(format, string):
    return datetime.fromtimestamp(mktime(strptime(string, format)))


class FitsHeaderEntry(Base):
    __tablename__ = 'fitsheaderentries'

    dbentry_id = Column(Integer, ForeignKey('data.id'))
    id = Column(Integer, primary_key=True)
    key = Column(String, nullable=False)
    value = Column(String, nullable=False)

    def __init__(self, key, value):
        self.key = key
        self.value = value

    def __eq__(self, other):
        return (
            (self.id == other.id or self.id is None or other.id is None) and
            self.key == other.key and
            self.value == other.value)

    def __ne__(self, other):
        return not (self == other)

    def __repr__(self):  # pragma: no cover
        return '<%s(id %s, key %r, value %r)>' % (
            self.__class__.__name__, self.id, self.key, self.value)


class Tag(Base):
    __tablename__ = 'tags'

    name = Column(String, nullable=False, primary_key=True)

    def __init__(self, name):
        self.name = name

    def __eq__(self, other):
        return self.name == other.name

    def __ne__(self, other):
        return not (self == other)

    def __str__(self):
        return self.name

    def __repr__(self):  # pragma: no cover
        return '<%s(name %r)>' % (self.__class__.__name__, self.name)


class DatabaseEntry(Base):
    """
    DatabaseEntry()

    The class :class:`DatabaseEntry` represents the main table of the database
    and each instance represents one record that *can* be saved in the
    database.

    Attributes
    ----------
    id : int
        A unique ID number. By default it is None, but automatically set to the
        maximum number plus one when this entry is added to the database.
    source : string
        The source is the name of an observatory or the name of a network of
        observatories.
    provider : string
        The name of the server which provides the retrieved data.
    physobs : string
        A physical observable identifier used by VSO.
    fileid : string
        The file ID is a string defined by the data provider that should point
        to a specific data product. The association of fileid to the specific
        data may change sometime, if the fileid always points to the latest
        calibrated data.
    observation_time_start : datetime
        The date and time when the observation of the data started.
    observation_time_end : datetime
        The date and time when the observation of the data ended.
    instrument : string
        The instrument which was used to observe the data.
    size : float
        The size of the data in kilobytes.
    wavemin : float
        The value of the measured wave length.
    wavemax : float
        This is the same value as ``wavemin``. The value is stored twice,
        because each ``suds.sudsobject.QueryResponseBlock`` which is used by
        the vso package contains both these values.
    path : string
        A local file path where the according FITS file is saved.
    download_time : datetime
        The date and time when the files connected to a query have been
        downloaded. Note: this is not the date and time when this entry has
        been added to a database!
    starred : bool
        Entries can be starred to mark them. By default, this value is False.
    fits_header_entries : list
        A list of ``FitsHeaderEntry`` instances.
    tags : list
        A list of ``Tag`` instances. Use :ref:`sunpy.database.Database.tag` to
        add a new tag or multiple tags to a specific entry.

    """
    __tablename__ = 'data'

    # FIXME: primary key is data provider + file ID + download_time!
    id = Column(Integer, primary_key=True)
    source = Column(String)
    provider = Column(String)
    physobs = Column(String)
    fileid = Column(String)
    observation_time_start = Column(DateTime)
    observation_time_end = Column(DateTime)
    instrument = Column(String)
    size = Column(Float)
    wavemin = Column(Float)
    wavemax = Column(Float)
    path = Column(String)
    download_time = Column(DateTime)
    starred = Column(Boolean, default=False)
    fits_header_entries = relationship('FitsHeaderEntry', backref='data')
    tags = relationship('Tag', secondary=association_table, backref='data')

    @classmethod
    def from_query_result_block(cls, qr_block, default_waveunit=None):
        """Make a new :class:`DatabaseEntry` instance from a VSO query result
        block. The values of :attr:`wavemin` and :attr:`wavemax` are converted
        to nm (nanometres).

        Parameters
        ----------
        qr_block : suds.sudsobject.QueryResponseBlock
            A query result block is usually not created directly; instead,
            one gets instances of ``suds.sudsobject.QueryResponseBlock`` by
            iterating over a VSO query result.
        default_waveunit : str, optional
            The wavelength unit that is used if it cannot be found in the
            `qr_block`.

        Examples
        --------
        >>> from sunpy.net import vso
        >>> from sunpy.database import DatabaseEntry
        >>> client = vso.VSOClient()
        >>> qr = client.query(vso.attrs.Time('2001/1/1', '2001/1/2'), vso.attrs.Instrument('eit'))
        >>> DatabaseEntry.from_query_result_block(qr[0])
        <DatabaseEntry(id None, data provider SDAC, fileid /archive/soho/private/data/processed/eit/lz/2001/01/efz20010101.010014)>

        """
        time_start = timestamp2datetime('%Y%m%d%H%M%S', qr_block.time.start)
        time_end = timestamp2datetime('%Y%m%d%H%M%S', qr_block.time.end)
        wave = qr_block.wave
        if wave.waveunit is None:
            if default_waveunit is None:
                raise WaveunitNotFoundError(qr_block)
            else:
                unit = Unit(default_waveunit)
        else:
            unit = Unit(wave.waveunit)
        if wave.wavemin is None:
            wavemin = None
        else:
            wavemin = unit.to(nm, float(wave.wavemin), equivalencies.spectral())
        if wave.wavemax is None:
            wavemax = None
        else:
            wavemax = unit.to(nm, float(wave.wavemax), equivalencies.spectral())
        source = str(qr_block.source) if qr_block.source is not None else None
        provider = str(qr_block.provider) if qr_block.provider is not None else None
        fileid = str(qr_block.fileid) if qr_block.fileid is not None else None
        instrument = str(qr_block.instrument) if qr_block.instrument is not None else None
        physobs = getattr(qr_block, 'physobs', None)
        return cls(
            source=source, provider=provider, physobs=physobs, fileid=fileid,
            observation_time_start=time_start, observation_time_end=time_end,
            instrument=instrument, size=qr_block.size,
            wavemin=wavemin, wavemax=wavemax)

    @classmethod
    def from_fits_filepath(cls, path, default_waveunit=None):
        """Make a new :class:`DatabaseEntry` instance by using the method
        :meth:`add_fits_header_entries_from_file`. This classmethod is simply a
        shortcut for the following lines::

            entry = DatabaseEntry()
            entry.add_fits_header_entries_from_file(path, default_waveunit)

        """
        entry = cls()
        entry.add_fits_header_entries_from_file(path, default_waveunit)
        return entry

    def add_fits_header_entries_from_file(self, fits_filepath,
            default_waveunit=None):
        """Use the header of a FITS file to add this information to this
        database entry. It will be saved in the attribute
        :attr:`fits_header_entries`. If the key INSTRUME, WAVELNTH or
        DATE-OBS / DATE_OBS is available, the attribute :attr:`instrument`,
        :attr:`wavemin` and :attr:`wavemax` or :attr:`observation_time_start`
        is set, respectively. If the wavelength unit can be read, the values of
        of :attr:`wavemin` and :attr:`wavemax` are converted to nm
        (nanometres).

        Parameters
        ----------
        fits_filepath : file path or file-like object
            File to get header from.  If an opened file object, its mode
            must be one of the following rb, rb+, or ab+.

        default_waveunit : str, optional
            The wavelength unit that is used for a header if it cannot be
            found.

        Raises
        ------
        sunpy.database.WaveunitNotFoundError
            If `default_waveunit` is not given and the wavelength unit cannot
            be found in one of the FITS headers

        sunpy.WaveunitNotConvertibleError
            If a wavelength unit could be found but cannot be used to create an
            instance of the type ``astropy.units.Unit``. This can be the case
            for example if a FITS header has the key `WAVEUNIT` with the value
            `nonsense`.

        Examples
        --------
        >>> from pprint import pprint
        >>> from sunpy.database import DatabaseEntry
        >>> import sunpy
        >>> entry = DatabaseEntry()
        >>> entry.fits_header_entries
        []
        >>> entry.add_fits_header_entries_from_file(sunpy.RHESSI_EVENT_LIST)
        >>> pprint(entry.fits_header_entries)
        [<FitsHeaderEntry(id None, key 'SIMPLE', value True)>,
         <FitsHeaderEntry(id None, key 'BITPIX', value 8)>,
         <FitsHeaderEntry(id None, key 'NAXIS', value 0)>,
         <FitsHeaderEntry(id None, key 'EXTEND', value True)>,
         <FitsHeaderEntry(id None, key 'DATE', value '2011-09-13T15:37:38')>,
         <FitsHeaderEntry(id None, key 'ORIGIN', value 'RHESSI')>,
         <FitsHeaderEntry(id None, key 'OBSERVER', value 'Unknown')>,
         <FitsHeaderEntry(id None, key 'TELESCOP', value 'RHESSI')>,
         <FitsHeaderEntry(id None, key 'INSTRUME', value 'RHESSI')>,
         <FitsHeaderEntry(id None, key 'OBJECT', value 'Sun')>,
         <FitsHeaderEntry(id None, key 'DATE_OBS', value '2002-02-20T11:06:00.000')>,
         <FitsHeaderEntry(id None, key 'DATE_END', value '2002-02-20T11:06:43.330')>,
         <FitsHeaderEntry(id None, key 'TIME_UNI', value 1)>,
         <FitsHeaderEntry(id None, key 'ENERGY_L', value 25.0)>,
         <FitsHeaderEntry(id None, key 'ENERGY_H', value 40.0)>,
         <FitsHeaderEntry(id None, key 'TIMESYS', value '1979-01-01T00:00:00')>,
         <FitsHeaderEntry(id None, key 'TIMEUNIT', value 'd')>]

        """
        # FIXME: store a list of headers and not only the first one!
        header = fits.get_header(fits_filepath)[0]
        for key, value in header.iteritems():
            # Yes, it is possible to have an empty key in a FITS file.
            # Example: sunpy.data.sample.EIT_195_IMAGE
            # Don't ask me why this could be a good idea.
            if key in ('KEYCOMMENTS', ''):
                value = str(value)
            self.fits_header_entries.append(FitsHeaderEntry(key, value))
        waveunit = fits.extract_waveunit(header)
        if waveunit is None:
            if default_waveunit is None:
                raise WaveunitNotFoundError(fits_filepath)
            else:
                waveunit = default_waveunit
        try:
            unit = Unit(waveunit)
        except ValueError:
            raise WaveunitNotConvertibleError(waveunit)
        for header_entry in self.fits_header_entries:
            key, value = header_entry.key, header_entry.value
            if key == 'INSTRUME':
                self.instrument = value
            elif key == 'WAVELNTH':
                # use the value of `unit` to convert the wavelength to nm
                self.wavemin = self.wavemax = unit.to(
                    nm, value, equivalencies.spectral())
            # NOTE: the key DATE-END or DATE_END is not part of the official
            # FITS standard, but many FITS files use it in their header
            elif key in ('DATE-END', 'DATE_END'):
                self.observation_time_end = parse_time(value)
            elif key in ('DATE-OBS', 'DATE_OBS'):
                self.observation_time_start = parse_time(value)

    def __eq__(self, other):
        wavemins_equal = self.wavemin is None and other.wavemin is None or\
                self.wavemin is not None and other.wavemin is not None and\
                round(self.wavemin, 10) == round(other.wavemin, 10)
        wavemaxs_equal = self.wavemax is None and other.wavemax is None or\
                self.wavemax is not None and other.wavemax is not None and\
                round(self.wavemax, 10) == round(other.wavemax, 10)
        return (
            (self.id == other.id or self.id is None or other.id is None) and
            self.source == other.source and
            self.provider == other.provider and
            self.physobs == other.physobs and
            self.fileid == other.fileid and
            self.observation_time_start == other.observation_time_start and
            self.observation_time_end == other.observation_time_end and
            self.instrument == other.instrument and
            self.size == other.size and
            wavemins_equal and
            wavemaxs_equal and
            self.path == other.path and
            self.download_time == other.download_time and
            bool(self.starred) == bool(other.starred) and
            self.fits_header_entries == other.fits_header_entries and
            self.tags == other.tags)

    def __ne__(self, other):  # pragma: no cover
        return not (self == other)

    def __repr__(self):  # pragma: no cover
        attrs = [
            'id', 'source', 'provider', 'physobs', 'fileid',
            'observation_time_start', 'observation_time_end', 'instrument',
            'size', 'wavemin', 'wavemax', 'path', 'download_time', 'starred',
            'fits_header_entries', 'tags']
        ret = '<%s(' % (self.__class__.__name__,)
        for attr in attrs:
            value = getattr(self, attr, None)
            if value:
                ret += '%s %r, ' % (attr, value)
        ret = ret.rstrip(', ')
        ret += ')>'
        return ret


def entries_from_query_result(qr, default_waveunit=None):
    """Use a query response returned from :meth:`sunpy.net.vso.VSOClient.query`
    or :meth:`sunpy.net.vso.VSOClient.query_legacy` to generate instances of
    :class:`DatabaseEntry`. Return an iterator over those instances.

    Parameters
    ----------
    qr : sunpy.net.vso.vso.QueryResponse
        The query response from which to build the database entries.

    default_waveunit : str, optional
        See :meth:`sunpy.database.DatabaseEntry.from_query_result_block`.

    Examples
    --------
    >>> from sunpy.net import vso
    >>> from sunpy.database import entries_from_query_result
    >>> client = vso.VSOClient()
    >>> qr = client.query(vso.attrs.Time('2001/1/1', '2001/1/2'), vso.attrs.Instrument('eit'))
    >>> entries = entries_from_query_result(qr)
    >>> entries.next()
    <DatabaseEntry(id None, data provider SDAC, fileid /archive/soho/private/data/processed/eit/lz/2001/01/efz20010101.010014)>

    """
    for block in qr:
        yield DatabaseEntry.from_query_result_block(block, default_waveunit)


def entries_from_dir(fitsdir, recursive=False, pattern='*',
        default_waveunit=None):
    """Search the given directory for FITS files and use the corresponding FITS
    headers to generate instances of :class:`DatabaseEntry`. FITS files are
    detected by reading the content of each file, the `pattern` argument may be
    used to avoid reading entire directories if one knows that all FITS files
    have the same filename extension.

    Parameters
    ----------
    fitsdir : string
        The directory where to look for FITS files.

    recursive : bool, optional
        If True, the given directory will be searched recursively. Otherwise,
        only the given directory and no subdirectories are searched. The
        default is `False`, i.e. the given directory is not searched
        recursively.

    pattern : string, optional
        The pattern can be used to filter the list of filenames before the
        files are attempted to be read. The default is to collect all files.
        This value is passed to the function :func:`fnmatch.filter`, see its
        documentation for more information on the supported syntax.

    default_waveunit : str, optional
        See
        :meth:`sunpy.database.tables.DatabaseEntry.add_fits_header_entries_from_file`.

    Returns
    -------
    generator of (DatabaseEntry, str) pairs
        A generator where each item is a tuple consisting of a
        :class:`DatabaseEntry` instance and the absolute path to the filename
        which was used to make the database entry.

    Examples
    --------
    >>> from pprint import pprint
    >>> from sunpy.database import entries_from_path
    >>> from sunpy.data.test import rootdir as fitsdir
    >>> entries = list(entries_from_path(fitsdir))
    >>> len(entries)
    2
    >>> # and now search `fitsdir` recursive
    >>> entries = list(entries_from_path(fitsdir, True))
    >>> len(entries)
    15
    >>> # print the first 5 items of the FITS header of the first found file
    >>> first_entry, filename = entries[0]
    >>> pprint(first_entry.fits_header_entries[:5])
    [<FitsHeaderEntry(id None, key 'SIMPLE', value True)>,
     <FitsHeaderEntry(id None, key 'BITPIX', value -64)>,
     <FitsHeaderEntry(id None, key 'NAXIS', value 2)>,
     <FitsHeaderEntry(id None, key 'NAXIS1', value 128)>,
     <FitsHeaderEntry(id None, key 'NAXIS2', value 128)>]

    """
    for dirpath, dirnames, filenames in os.walk(fitsdir):
        filename_paths = (os.path.join(dirpath, name) for name in filenames)
        for path in fnmatch.filter(filename_paths, pattern):
            try:
                filetype = sunpy_filetools._detect_filetype(path)
            except (
                    sunpy_filetools.UnrecognizedFileTypeError,
                    sunpy_filetools.InvalidJPEG2000FileExtension):
                continue
            if filetype == fits:
                entry = DatabaseEntry.from_fits_filepath(
                    path, default_waveunit)
                yield entry, path
        if not recursive:
            break


def display_entries(database_entries, columns):
    """Generate a table to display the database entries.

    Parameters
    ----------
    database_entries : iterable of :class:`DatabaseEntry` instances
        The database entries will be the rows in the resulting table.

    columns : iterable of str
        The columns that will be displayed in the resulting table. Possible
        values for the strings are all attributes of :class:`DatabaseEntry`.

    Returns
    -------
    str
        A formatted table that can be printed on the console or written to a
        file.

    """
    header = [columns]
    rulers = [['-' * len(col) for col in columns]]
    data = []
    for entry in database_entries:
        row = []
        for col in columns:
            if col == 'starred':
                row.append('Yes' if entry.starred else 'No')
            elif col == 'tags':
                row.append(', '.join(imap(str, entry.tags)) or 'N/A')
            else:
                row.append(str(getattr(entry, col) or 'N/A'))
        if not row:
            raise TypeError('at least one column must be given')
        data.append(row)
    if not data:
        raise TypeError('given iterable is empty')
    return print_table(header + rulers + data)
