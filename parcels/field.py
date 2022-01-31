import collections
import datetime

import dask.array as da
import numpy as np
import xarray as xr
from pathlib import Path

from .fieldfilebuffer import (NetcdfFileBuffer, DeferredNetcdfFileBuffer,
                              DaskFileBuffer, DeferredDaskFileBuffer)
# from parcels._grid import CGrid
from .grid import Grid
# from .grid import GridCode
# from parcels.numba.grid.base import BaseGrid
from parcels.numba.grid.base import GridCode
from parcels.tools.converters import Geographic
from parcels.tools.converters import GeographicPolar
from parcels.tools.converters import TimeConverter
from parcels.tools.converters import UnitConverter
from parcels.tools.converters import unitconverters_map
from parcels.tools.statuscodes import FieldOutOfBoundError
from parcels.tools.statuscodes import FieldSamplingError
from parcels.tools.loggers import logger
from parcels.numba.field.vector_field_3d import NumbaVectorField3D
from parcels.numba.field.vector_field_2d import NumbaVectorField2D
from parcels.numba.field.utils import _get_numba_field_class


__all__ = ['Field', 'VectorField', 'SummedField', 'NestedField']


def _isParticle(key):
    if hasattr(key, '_next_dt'):
        return True
    else:
        return False


class Field():
    """Class that encapsulates access to field data (Python)

    It will contain a numba_field attribute that is Numba compatible
    All/most variables that are defined in the numba field can be accessed
    from the Python Field as well.

    :param name: Name of the field
    :param data: 2D, 3D or 4D numpy array of field data.

           1. If data shape is [xdim, ydim], [xdim, ydim, zdim], [xdim, ydim, tdim] or [xdim, ydim, zdim, tdim],
              whichever is relevant for the dataset, use the flag transpose=True
           2. If data shape is [ydim, xdim], [zdim, ydim, xdim], [tdim, ydim, xdim] or [tdim, zdim, ydim, xdim],
              use the flag transpose=False
           3. If data has any other shape, you first need to reorder it
    :param lon: Longitude coordinates (numpy vector or array) of the field (only if grid is None)
    :param lat: Latitude coordinates (numpy vector or array) of the field (only if grid is None)
    :param depth: Depth coordinates (numpy vector or array) of the field (only if grid is None)
    :param time: Time coordinates (numpy vector) of the field (only if grid is None)
    :param mesh: String indicating the type of mesh coordinates and
           units used during velocity interpolation: (only if grid is None)

           1. spherical: Lat and lon in degree, with a
              correction for zonal velocity U near the poles.
           2. flat (default): No conversion, lat/lon are assumed to be in m.
    :param timestamps: A numpy array containing the timestamps for each of the files in filenames, for loading
           from netCDF files only. Default is None if the netCDF dimensions dictionary includes time.
    :param grid: :class:`parcels._grid.Grid` object containing all the lon, lat depth, time
           mesh and time_origin information. Can be constructed from any of the Grid objects
    :param fieldtype: Type of Field to be used for UnitConverter when using SummedFields
           (either 'U', 'V', 'Kh_zonal', 'Kh_meridional' or None)
    :param transpose: Transpose data to required (lon, lat) layout
    :param vmin: Minimum allowed value on the field. Data below this value are set to zero
    :param vmax: Maximum allowed value on the field. Data above this value are set to zero
    :param cast_data_dtype: Cast Field data to dtype. Supported dtypes are np.float32 (default) and np.float64.
           Note that dtype can only be float32 in JIT mode
    :param time_origin: Time origin (TimeConverter object) of the time axis (only if grid is None)
    :param interp_method: Method for interpolation. Options are 'linear' (default), 'nearest',
           'linear_invdist_land_tracer', 'cgrid_velocity', 'cgrid_tracer' and 'bgrid_velocity'
    :param allow_time_extrapolation: boolean whether to allow for extrapolation in time
           (i.e. beyond the last available time snapshot)
    :param time_periodic: To loop periodically over the time component of the Field. It is set to either False or the length of the period (either float in seconds or datetime.timedelta object).
           The last value of the time series can be provided (which is the same as the initial one) or not (Default: False)
           This flag overrides the allow_time_interpolation and sets it to False
    :param chunkdims_name_map (opt.): gives a name map to the FieldFileBuffer that declared a mapping between chunksize name, NetCDF dimension and Parcels dimension;
           required only if currently incompatible OCM field is loaded and chunking is used by 'chunksize' (which is the default)
    :param to_write: Write the Field in NetCDF format at the same frequency as the ParticleFile outputdt,
           using a filenaming scheme based on the ParticleFile name

    For usage examples see the following tutorials:

    * `Nested Fields <https://nbviewer.jupyter.org/github/OceanParcels/parcels/blob/master/parcels/examples/tutorial_NestedFields.ipynb>`_

    * `Summed Fields <https://nbviewer.jupyter.org/github/OceanParcels/parcels/blob/master/parcels/examples/tutorial_SummedFields.ipynb>`_
    """
    def __init__(
            self, name, data, lon=None, lat=None, depth=None, time=None,
            grid=None, mesh='flat', timestamps=None, fieldtype=None,
            transpose=False, vmin=None, vmax=None, cast_data_dtype='float32',
            time_origin=None, interp_method='linear',
            allow_time_extrapolation=None, time_periodic=False,
            gridindexingtype='nemo', to_write=False, **kwargs):
        if grid is None:
            grid = Grid(lon, lat, depth, time, time_origin=time_origin, mesh=mesh)
        elif not isinstance(grid, Grid):
            grid = Grid.wrap(grid)
        elif grid.defer_load and isinstance(data, np.ndarray):
            raise ValueError('Cannot combine Grid from defer_loaded Field with'
                             ' np.ndarray data. please specify lon, lat, depth and time dimensions separately')
        self.gridindexingtype = gridindexingtype
        self.vmin = vmin
        self.vmax = vmax
        if not isinstance(name, tuple):
            self.name = name
            self.filebuffername = name
        else:
            self.name, self.filebuffername = name
        self.cast_data_dtype = cast_data_dtype
        if self.cast_data_dtype == 'float32':
            self.cast_data_dtype = np.float32
        elif self.cast_data_dtype == 'float64':
            self.cast_data_dtype = np.float64

        if not grid.defer_load:
            data = self.reshape(data, grid, transpose)

            # Hack around the fact that NaN and ridiculously large values
            # propagate in SciPy's interpolators
            lib = np if isinstance(data, np.ndarray) else da
            data[lib.isnan(data)] = 0.
            if self.vmin is not None:
                data[data < self.vmin] = 0.
            if self.vmax is not None:
                data[data > self.vmax] = 0.

            if grid._add_last_periodic_data_timestep:
                data = lib.concatenate((self.data, self.data[:1, :]), axis=0)
        self.numba_class = _get_numba_field_class(grid)
        self.numba_field = self.numba_class(grid.numba_grid, data,
                                            interp_method=interp_method,
                                            time_periodic=time_periodic)
        self.grid = grid

        self.igrid = -1
        # self.lon, self.lat, self.depth and self.time are not used anymore in parcels.
        # self.grid should be used instead.
        # Those variables are still defined for backwards compatibility with users codes.
        # They are implemented with __getattr__
        self.fieldtype = self.name if fieldtype is None else fieldtype
        self.to_write = to_write
        if self.grid.mesh == 'flat' or (self.fieldtype not in unitconverters_map.keys()):
            self.units = UnitConverter()
        elif self.grid.mesh == 'spherical':
            self.units = unitconverters_map[self.fieldtype]
        else:
            raise ValueError("Unsupported mesh type. Choose either: 'spherical' or 'flat'")
        self.timestamps = timestamps
        if type(interp_method) is dict:
            if self.name in interp_method:
                self.interp_method = interp_method[self.name]
            else:
                raise RuntimeError('interp_method is a dictionary but %s is not in it' % name)
        else:
            self.interp_method = interp_method
        self.gridindexingtype = gridindexingtype
        if self.interp_method in ['bgrid_velocity', 'bgrid_w_velocity', 'bgrid_tracer'] and \
           self.grid.gtype in [GridCode.RectilinearSGrid, GridCode.CurvilinearSGrid]:
            logger.warning_once('General s-levels are not supported in B-grid. RectilinearSGrid and CurvilinearSGrid can still be used to deal with shaved cells, but the levels must be horizontal.')

        self.fieldset = None
        if allow_time_extrapolation is None:
            self.allow_time_extrapolation = True if len(self.grid.time) == 1 else False
        else:
            self.allow_time_extrapolation = allow_time_extrapolation

        self.time_periodic = time_periodic
        if self.time_periodic is not False and self.allow_time_extrapolation:
            logger.warning_once("allow_time_extrapolation and time_periodic cannot be used together.\n \
                                 allow_time_extrapolation is set to False")
            self.allow_time_extrapolation = False
        if self.time_periodic is True:
            raise ValueError("Unsupported time_periodic=True. time_periodic must now be either False or the length of the period (either float in seconds or datetime.timedelta object.")
        if self.time_periodic is not False:
            if isinstance(self.time_periodic, datetime.timedelta):
                self.time_periodic = self.time_periodic.total_seconds()
            if not np.isclose(self.grid.time[-1] - self.grid.time[0], self.time_periodic):
                if self.grid.time[-1] - self.grid.time[0] > self.time_periodic:
                    raise ValueError("Time series provided is longer than the time_periodic parameter")
                self.grid._add_last_periodic_data_timestep = True
                self.grid.time = np.append(self.grid.time, self.grid.time[0] + self.time_periodic)
                self.grid.time_full = self.grid.time

        self._scaling_factor = None

        # Variable names in JIT code
        self.dimensions = kwargs.pop('dimensions', None)
        self.indices = kwargs.pop('indices', None)
        self.dataFiles = kwargs.pop('dataFiles', None)
        if self.grid._add_last_periodic_data_timestep and self.dataFiles is not None:
            self.dataFiles = np.append(self.dataFiles, self.dataFiles[0])
        self._field_fb_class = kwargs.pop('FieldFileBuffer', None)
        self.netcdf_engine = kwargs.pop('netcdf_engine', 'netcdf4')
        self.loaded_time_indices = []
        self.creation_log = kwargs.pop('creation_log', '')
        self.chunksize = kwargs.pop('chunksize', None)
        self.netcdf_chunkdims_name_map = kwargs.pop('chunkdims_name_map', None)
#         self.grid.depth_field = kwargs.pop('depth_field', None)

#         if self.grid.depth_field == 'not_yet_set':
#             assert self.grid.z4d, 'Providing the depth dimensions from another field data is only available for 4d S grids'

        # data_full_zdim is the vertical dimension of the complete field data, ignoring the indices.
        # (data_full_zdim = grid.zdim if no indices are used, for A- and C-grids and for some B-grids). It is used for the B-grid,
        # since some datasets do not provide the deeper level of data (which is ignored by the interpolation).
        self.data_full_zdim = kwargs.pop('data_full_zdim', None)
        self.data_chunks = []
        self.nchunks = []
        self.chunk_set = False
        self.filebuffers = [None] * 2
        if len(kwargs) > 0:
            raise SyntaxError('Field received an unexpected keyword argument "%s"' % list(kwargs.keys())[0])

    def __getattr__(self, key):
        if key == "numba_field":
            raise AttributeError("Cannot get numba field vars before creating it.")
        if key in ["data", "interp_method", "time_periodic", "allow_time_extrapolation"]:
            return getattr(self.numba_field, key)
        if key in ["lat", "lon", "depth"]:
            return getattr(self.grid, key)
        raise AttributeError(f"Attribute {key} not found.")

    def __setattr__(self, key, value):
        if key in ["data", "interp_method", "time_periodic", "allow_time_extrapolation"]:
            setattr(self.numba_field, key, value)
        elif key in ["lat", "lon", "depth"]:
            setattr(self.grid, key, value)
        else:
            super().__setattr__(key, value)

    @classmethod
    def get_dim_filenames(cls, filenames, dim):
        if isinstance(filenames, str) or not isinstance(filenames, collections.abc.Iterable):
            return [filenames]
        elif isinstance(filenames, dict):
            assert dim in filenames.keys(), \
                'filename dimension keys must be lon, lat, depth or data'
            filename = filenames[dim]
            if isinstance(filename, str):
                return [filename]
            else:
                return filename
        else:
            return filenames

    @staticmethod
    def collect_timeslices(timestamps, data_filenames, _grid_fb_class, dimensions, indices, netcdf_engine):
        if timestamps is not None:
            dataFiles = []
            for findex in range(len(data_filenames)):
                for f in [data_filenames[findex], ] * len(timestamps[findex]):
                    dataFiles.append(f)
            timeslices = np.array([stamp for file in timestamps for stamp in file])
            time = timeslices
        else:
            timeslices = []
            dataFiles = []
            for fname in data_filenames:
                with _grid_fb_class(fname, dimensions, indices, netcdf_engine=netcdf_engine) as filebuffer:
                    ftime = filebuffer.time
                    timeslices.append(ftime)
                    dataFiles.append([fname] * len(ftime))
            timeslices = np.array(timeslices)
            time = np.concatenate(timeslices)
            dataFiles = np.concatenate(np.array(dataFiles))
        if time.size == 1 and time[0] is None:
            time[0] = 0
        time_origin = TimeConverter(time[0])
        time = time_origin.reltime(time)

        if not np.all((time[1:] - time[:-1]) > 0):
            id_not_ordered = np.where(time[1:] < time[:-1])[0][0]
            raise AssertionError(
                'Please make sure your netCDF files are ordered in time. First pair of non-ordered files: %s, %s'
                % (dataFiles[id_not_ordered], dataFiles[id_not_ordered + 1]))
        return time, time_origin, timeslices, dataFiles

    @classmethod
    def from_netcdf(cls, filenames, variable, dimensions, indices=None, grid=None,
                    mesh='spherical', timestamps=None, allow_time_extrapolation=None, time_periodic=False,
                    deferred_load=True, **kwargs):
        """Create field from netCDF file

        :param filenames: list of filenames to read for the field. filenames can be a list [files] or
               a dictionary {dim:[files]} (if lon, lat, depth and/or data not stored in same files as data)
               In the latetr case, time values are in filenames[data]
        :param variable: Tuple mapping field name to variable name in the NetCDF file.
        :param dimensions: Dictionary mapping variable names for the relevant dimensions in the NetCDF file
        :param indices: dictionary mapping indices for each dimension to read from file.
               This can be used for reading in only a subregion of the NetCDF file.
               Note that negative indices are not allowed.
        :param mesh: String indicating the type of mesh coordinates and
               units used during velocity interpolation:

               1. spherical (default): Lat and lon in degree, with a
                  correction for zonal velocity U near the poles.
               2. flat: No conversion, lat/lon are assumed to be in m.
        :param timestamps: A numpy array of datetime64 objects containing the timestamps for each of the files in filenames.
               Default is None if dimensions includes time.
        :param allow_time_extrapolation: boolean whether to allow for extrapolation in time
               (i.e. beyond the last available time snapshot)
               Default is False if dimensions includes time, else True
        :param time_periodic: boolean whether to loop periodically over the time component of the FieldSet
               This flag overrides the allow_time_interpolation and sets it to False
        :param deferred_load: boolean whether to only pre-load data (in deferred mode) or
               fully load them (default: True). It is advised to deferred load the data, since in
               that case Parcels deals with a better memory management during particle set execution.
               deferred_load=False is however sometimes necessary for plotting the fields.
        :param gridindexingtype: The type of gridindexing. Either 'nemo' (default) or 'mitgcm' are supported.
               See also the Grid indexing documentation on oceanparcels.org
        :param chunksize: size of the chunks in dask loading

        For usage examples see the following tutorial:

        * `Timestamps <https://nbviewer.jupyter.org/github/OceanParcels/parcels/blob/master/parcels/examples/tutorial_timestamps.ipynb>`_
        """
        # Ensure the timestamps array is compatible with the user-provided datafiles.
        if timestamps is not None:
            if isinstance(filenames, list):
                assert len(filenames) == len(timestamps), 'Outer dimension of timestamps should correspond to number of files.'
            elif isinstance(filenames, dict):
                for k in filenames.keys():
                    if k not in ['lat', 'lon', 'depth', 'time']:
                        assert(len(filenames[k]) == len(timestamps)), 'Outer dimension of timestamps should correspond to number of files.'
            else:
                raise TypeError("Filenames type is inconsistent with manual timestamp provision."
                                + "Should be dict or list")

        if isinstance(variable, str):  # for backward compatibility with Parcels < 2.0.0
            variable = (variable, variable)
        assert len(variable) == 2, 'The variable tuple must have length 2. Use FieldSet.from_netcdf() for multiple variables'

        data_filenames = cls.get_dim_filenames(filenames, 'data')
        lonlat_filename = cls.get_dim_filenames(filenames, 'lon')
        if isinstance(filenames, dict):
            assert len(lonlat_filename) == 1
        if lonlat_filename != cls.get_dim_filenames(filenames, 'lat'):
            raise NotImplementedError('longitude and latitude dimensions are currently processed together from one single file')
        lonlat_filename = lonlat_filename[0]
        if 'depth' in dimensions:
            depth_filename = cls.get_dim_filenames(filenames, 'depth')
            if isinstance(filenames, dict) and len(depth_filename) != 1:
                raise NotImplementedError('Vertically adaptive meshes not implemented for from_netcdf()')
            depth_filename = depth_filename[0]

        netcdf_engine = kwargs.pop('netcdf_engine', 'netcdf4')

        indices = {} if indices is None else indices.copy()
        for ind in indices:
            if len(indices[ind]) == 0:
                raise RuntimeError('Indices for %s can not be empty' % ind)
            assert np.min(indices[ind]) >= 0, \
                ('Negative indices are currently not allowed in Parcels. '
                 + 'This is related to the non-increasing dimension it could generate '
                 + 'if the domain goes from lon[-4] to lon[6] for example. '
                 + 'Please raise an issue on https://github.com/OceanParcels/parcels/issues '
                 + 'if you would need such feature implemented.')

        interp_method = kwargs.pop('interp_method', 'linear')
        if type(interp_method) is dict:
            if variable[0] in interp_method:
                interp_method = interp_method[variable[0]]
            else:
                raise RuntimeError('interp_method is a dictionary but %s is not in it' % variable[0])

        _grid_fb_class = NetcdfFileBuffer

        with _grid_fb_class(lonlat_filename, dimensions, indices, netcdf_engine) as filebuffer:
            lon, lat = filebuffer.lonlat
            indices = filebuffer.indices
            # Check if parcels_mesh has been explicitly set in file
            if 'parcels_mesh' in filebuffer.dataset.attrs:
                mesh = filebuffer.dataset.attrs['parcels_mesh']

        if 'depth' in dimensions:
            with _grid_fb_class(depth_filename, dimensions, indices, netcdf_engine, interp_method=interp_method) as filebuffer:
                filebuffer.name = filebuffer.parse_name(variable[1])
                if dimensions['depth'] == 'not_yet_set':
                    depth = filebuffer.depth_dimensions
                    kwargs['depth_field'] = 'not_yet_set'
                else:
                    depth = filebuffer.depth
                data_full_zdim = filebuffer.data_full_zdim
        else:
            indices['depth'] = [0]
            depth = np.zeros(1)
            data_full_zdim = 1

        kwargs['data_full_zdim'] = data_full_zdim

        if len(data_filenames) > 1 and 'time' not in dimensions and timestamps is None:
            raise RuntimeError('Multiple files given but no time dimension specified')

        if grid is None:
            # Concatenate time variable to determine overall dimension
            # across multiple files
            time, time_origin, timeslices, dataFiles = cls.collect_timeslices(timestamps, data_filenames,
                                                                              _grid_fb_class, dimensions,
                                                                              indices, netcdf_engine)
            grid = Grid(lon, lat, depth, time, time_origin=time_origin, mesh=mesh)
            grid.timeslices = timeslices
            kwargs['dataFiles'] = dataFiles
        elif grid is not None and ('dataFiles' not in kwargs or kwargs['dataFiles'] is None):
            # ==== means: the field has a shared grid, but may have different data files, so we need to collect the
            # ==== correct file time series again.
            _, _, _, dataFiles = cls.collect_timeslices(timestamps, data_filenames, _grid_fb_class,
                                                        dimensions, indices, netcdf_engine)
            kwargs['dataFiles'] = dataFiles

        chunksize = kwargs.get('chunksize', None)
        grid.chunksize = chunksize

        if 'time' in indices:
            logger.warning_once('time dimension in indices is not necessary anymore. It is then ignored.')

        if 'full_load' in kwargs:  # for backward compatibility with Parcels < v2.0.0
            deferred_load = not kwargs['full_load']

        if grid.time.size <= 2 or deferred_load is False:
            deferred_load = False

        if chunksize not in [False, None]:
            if deferred_load:
                _field_fb_class = DeferredDaskFileBuffer
            else:
                _field_fb_class = DaskFileBuffer
        elif deferred_load:
            _field_fb_class = DeferredNetcdfFileBuffer
        else:
            _field_fb_class = NetcdfFileBuffer
        kwargs['FieldFileBuffer'] = _field_fb_class

        if not deferred_load:
            # Pre-allocate data before reading files into buffer
            data_list = []
            ti = 0
            for tslice, fname in zip(grid.timeslices, data_filenames):
                with _field_fb_class(fname, dimensions, indices, netcdf_engine,
                                     interp_method=interp_method, data_full_zdim=data_full_zdim,
                                     chunksize=chunksize) as filebuffer:
                    # If Field.from_netcdf is called directly, it may not have a 'data' dimension
                    # In that case, assume that 'name' is the data dimension
                    filebuffer.name = filebuffer.parse_name(variable[1])
                    buffer_data = filebuffer.data
                    if len(buffer_data.shape) == 2:
                        data_list.append(buffer_data.reshape(sum(((len(tslice), 1), buffer_data.shape), ())))
                    elif len(buffer_data.shape) == 3:
                        if len(filebuffer.indices['depth']) > 1:
                            data_list.append(buffer_data.reshape(sum(((1,), buffer_data.shape), ())))
                        else:
                            if type(tslice) not in [list, np.ndarray, da.Array, xr.DataArray]:
                                tslice = [tslice]
                            data_list.append(buffer_data.reshape(sum(((len(tslice), 1), buffer_data.shape[1:]), ())))
                    else:
                        data_list.append(buffer_data)
                    if type(tslice) not in [list, np.ndarray, da.Array, xr.DataArray]:
                        tslice = [tslice]
                ti += len(tslice)
            lib = np if isinstance(data_list[0], np.ndarray) else da
            data = lib.concatenate(data_list, axis=0)
        else:
            grid.defer_load = True
            grid.ti = -1
            data = DeferredArray()
            data.compute_shape(grid.xdim, grid.ydim, grid.zdim, grid.tdim, len(grid.timeslices))

        if allow_time_extrapolation is None:
            allow_time_extrapolation = False if 'time' in dimensions else True

        kwargs['dimensions'] = dimensions.copy()
        kwargs['indices'] = indices
        kwargs['time_periodic'] = time_periodic
        kwargs['netcdf_engine'] = netcdf_engine

        return cls(variable, data, grid=grid, timestamps=timestamps,
                   allow_time_extrapolation=allow_time_extrapolation, interp_method=interp_method, **kwargs)

    @classmethod
    def from_xarray(cls, da, name, dimensions, mesh='spherical', allow_time_extrapolation=None,
                    time_periodic=False, **kwargs):
        """Create field from xarray Variable

        :param da: Xarray DataArray
        :param name: Name of the Field
        :param dimensions: Dictionary mapping variable names for the relevant dimensions in the DataArray
        :param mesh: String indicating the type of mesh coordinates and
               units used during velocity interpolation:

               1. spherical (default): Lat and lon in degree, with a
                  correction for zonal velocity U near the poles.
               2. flat: No conversion, lat/lon are assumed to be in m.
        :param allow_time_extrapolation: boolean whether to allow for extrapolation in time
               (i.e. beyond the last available time snapshot)
               Default is False if dimensions includes time, else True
        :param time_periodic: boolean whether to loop periodically over the time component of the FieldSet
               This flag overrides the allow_time_interpolation and sets it to False
        """

        data = da.data
        interp_method = kwargs.pop('interp_method', 'linear')

        time = da[dimensions['time']].values if 'time' in dimensions else np.array([0])
        depth = da[dimensions['depth']].values if 'depth' in dimensions else np.array([0])
        lon = da[dimensions['lon']].values
        lat = da[dimensions['lat']].values

        time_origin = TimeConverter(time[0])
        time = time_origin.reltime(time)

        grid = Grid(lon, lat, depth, time, time_origin=time_origin, mesh=mesh)
        return cls(name, data, grid=grid, allow_time_extrapolation=allow_time_extrapolation,
                   interp_method=interp_method, **kwargs)

    def reshape(self, data, grid, transpose=False):
        # Ensure that field data is the right data type
        if not isinstance(data, (np.ndarray, da.core.Array)):
            data = np.array(data)
        if (self.cast_data_dtype == np.float32) and (data.dtype != np.float32):
            data = data.astype(np.float32)
        elif (self.cast_data_dtype == np.float64) and (data.dtype != np.float64):
            data = data.astype(np.float64)
        lib = np if isinstance(data, np.ndarray) else da
        if transpose:
            data = lib.transpose(data)
        if grid.lat_flipped:
            data = lib.flip(data, axis=-2)

        if grid.xdim == 1 or grid.ydim == 1:
            data = lib.squeeze(data)  # First remove all length-1 dimensions in data, so that we can add them below
        if grid.xdim == 1 and len(data.shape) < 4:
            if lib == da:
                raise NotImplementedError('Length-one dimensions with field chunking not implemented, as dask does not have an `expand_dims` method. Use chunksize=None')
            data = lib.expand_dims(data, axis=-1)
        if grid.ydim == 1 and len(data.shape) < 4:
            if lib == da:
                raise NotImplementedError('Length-one dimensions with field chunking not implemented, as dask does not have an `expand_dims` method. Use chunksize=None')
            data = lib.expand_dims(data, axis=-2)

        if grid.tdim == 1:
            if len(data.shape) < 4:
                data = data.reshape(sum(((1,), data.shape), ()))
        if grid.zdim == 1:
            if len(data.shape) == 4:
                data = data.reshape(sum(((data.shape[0],), data.shape[2:]), ()))

        if len(data.shape) == 4:
            errormessage = ('Field %s expecting a data shape of [tdim, zdim, ydim, xdim]. '
                            'Flag transpose=True could help to reorder the data.' % self.name)

            assert data.shape[0] == grid.tdim, errormessage
            assert data.shape[2] == grid.ydim - 2 * grid.meridional_halo, errormessage
            assert data.shape[3] == grid.xdim - 2 * grid.zonal_halo, errormessage
            if self.gridindexingtype == 'pop':
                assert data.shape[1] == grid.zdim or data.shape[1] == grid.zdim-1, errormessage
            else:
                assert data.shape[1] == grid.zdim, errormessage
        else:
            assert (data.shape == (grid.tdim,
                                   grid.ydim - 2 * grid.meridional_halo,
                                   grid.xdim - 2 * grid.zonal_halo)), \
                ('Field %s expecting a data shape of [tdim, ydim, xdim]. '
                 'Flag transpose=True could help to reorder the data.' % self.name)
            data = data.reshape(data.shape[0], 1, data.shape[1], data.shape[2])
        if grid.meridional_halo > 0 or grid.zonal_halo > 0:
            data = self.add_periodic_halo(
                zonal=grid.zonal_halo > 0, meridional=grid.meridional_halo > 0,
                halosize=max(grid.meridional_halo, grid.zonal_halo), data=data)
        return data

    def set_scaling_factor(self, factor):
        """Scales the field data by some constant factor.

        :param factor: scaling factor

        For usage examples see the following tutorial:

        * `Unit converters <https://nbviewer.jupyter.org/github/OceanParcels/parcels/blob/master/parcels/examples/tutorial_unitconverters.ipynb>`_
        """
        if self._scaling_factor:
            raise NotImplementedError(('Scaling factor for field %s already defined.' % self.name))
        self._scaling_factor = factor
        if not self.grid.defer_load:
            self.data *= factor

    def set_depth_from_field(self, field):
        """Define the depth dimensions from another (time-varying) field

        See `this tutorial <https://nbviewer.jupyter.org/github/OceanParcels/parcels/blob/master/parcels/examples/tutorial_timevaryingdepthdimensions.ipynb>`_
        for a detailed explanation on how to set up time-evolving depth dimensions

        """
        self.grid.depth_field = field
        if self.grid != field.grid:
            field.grid.depth_field = field

    def calc_cell_edge_sizes(self):
        """Method to calculate cell sizes based on numpy.gradient method
                Currently only works for Rectilinear Grids"""
        if not self.grid.cell_edge_sizes:
            if self.grid.gtype in (GridCode.RectilinearZGrid, GridCode.RectilinearSGrid):
                self.grid.cell_edge_sizes['x'] = np.zeros((self.grid.ydim, self.grid.xdim), dtype=np.float32)
                self.grid.cell_edge_sizes['y'] = np.zeros((self.grid.ydim, self.grid.xdim), dtype=np.float32)

                x_conv = GeographicPolar() if self.grid.mesh == 'spherical' else UnitConverter()
                y_conv = Geographic() if self.grid.mesh == 'spherical' else UnitConverter()
                for y, (lat, dy) in enumerate(zip(self.grid.lat, np.gradient(self.grid.lat))):
                    for x, (lon, dx) in enumerate(zip(self.grid.lon, np.gradient(self.grid.lon))):
                        self.grid.cell_edge_sizes['x'][y, x] = x_conv.to_source(dx, lon, lat, self.grid.depth[0])
                        self.grid.cell_edge_sizes['y'][y, x] = y_conv.to_source(dy, lon, lat, self.grid.depth[0])
                self.cell_edge_sizes = self.grid.cell_edge_sizes
            else:
                logger.error(('Field.cell_edge_sizes() not implemented for ', self.grid.gtype, 'grids.',
                              'You can provide Field.grid.cell_edge_sizes yourself',
                              'by in e.g. NEMO using the e1u fields etc from the mesh_mask.nc file'))
                exit(-1)

    def cell_areas(self):
        """Method to calculate cell sizes based on cell_edge_sizes
                Currently only works for Rectilinear Grids"""
        if not self.grid.cell_edge_sizes:
            self.calc_cell_edge_sizes()
        return self.grid.cell_edge_sizes['x'] * self.grid.cell_edge_sizes['y']

    def reconnect_bnd_indices(self, xi, yi, xdim, ydim, sphere_mesh):
        if xi < 0:
            if sphere_mesh:
                xi = xdim-2
            else:
                xi = 0
        if xi > xdim-2:
            if sphere_mesh:
                xi = 0
            else:
                xi = xdim-2
        if yi < 0:
            yi = 0
        if yi > ydim-2:
            yi = ydim-2
            if sphere_mesh:
                xi = xdim - xi
        return xi, yi

    def __getitem__(self, key):
        if _isParticle(key):
            return self.numba_field.eval(key.time, key.depth, key.lat, key.lon, key)
        else:
            return self.numba_field.eval(*key)

    def get_block_id(self, block):
        return np.ravel_multi_index(block, self.nchunks)

    def get_block(self, bid):
        return np.unravel_index(bid, self.nchunks[1:])

    def chunk_setup(self):
        if isinstance(self.data, da.core.Array):
            chunks = self.data.chunks
            self.nchunks = self.data.numblocks
            npartitions = 1
            for n in self.nchunks[1:]:
                npartitions *= n
        elif isinstance(self.data, np.ndarray):
            chunks = tuple((t,) for t in self.data.shape)
            self.nchunks = (1,) * len(self.data.shape)
            npartitions = 1
        elif isinstance(self.data, DeferredArray):
            self.nchunks = (1,) * len(self.data.data_shape)
            return
        else:
            return

        self.data_chunks = [None] * npartitions
        self.c_data_chunks = [None] * npartitions
        # self.grid.load_chunk = np.zeros(npartitions, dtype=c_int)
        # self.grid.chunk_info format: number of dimensions (without tdim); number of chunks per dimensions;
        #      chunksizes (the 0th dim sizes for all chunk of dim[0], then so on for next dims
        self.grid.chunk_info = [[len(self.nchunks)-1], list(self.nchunks[1:]), sum(list(list(ci) for ci in chunks[1:]), [])]
        self.grid.chunk_info = sum(self.grid.chunk_info, [])
        self.chunk_set = True

    def chunk_data(self):
        if not self.chunk_set:
            self.chunk_setup()
        g = self.grid
        if isinstance(self.data, da.core.Array):
            for block_id in range(len(self.grid.load_chunk)):
                if g.load_chunk[block_id] == g.chunk_loading_requested \
                        or g.load_chunk[block_id] in g.chunk_loaded and self.data_chunks[block_id] is None:
                    block = self.get_block(block_id)
                    self.data_chunks[block_id] = np.array(self.data.blocks[(slice(self.grid.tdim),) + block])
                elif g.load_chunk[block_id] == g.chunk_not_loaded:
                    if isinstance(self.data_chunks, list):
                        self.data_chunks[block_id] = None
                    else:
                        self.data_chunks[block_id, :] = None
                    self.c_data_chunks[block_id] = None
        else:
            if isinstance(self.data_chunks, list):
                self.data_chunks[0] = None
            else:
                self.data_chunks[0, :] = None
            self.c_data_chunks[0] = None
            self.grid.load_chunk[0] = g.chunk_loaded_touched
            self.data_chunks[0] = np.array(self.data)

    def show(self, animation=False, show_time=None, domain=None, depth_level=0, projection=None, land=True,
             vmin=None, vmax=None, savefile=None, **kwargs):
        """Method to 'show' a Parcels Field

        :param animation: Boolean whether result is a single plot, or an animation
        :param show_time: Time at which to show the Field (only in single-plot mode)
        :param domain: dictionary (with keys 'N', 'S', 'E', 'W') defining domain to show
        :param depth_level: depth level to be plotted (default 0)
        :param projection: type of cartopy projection to use (default PlateCarree)
        :param land: Boolean whether to show land. This is ignored for flat meshes
        :param vmin: minimum colour scale (only in single-plot mode)
        :param vmax: maximum colour scale (only in single-plot mode)
        :param savefile: Name of a file to save the plot to
        """
        from parcels.plotting import plotfield
        plt, _, _, _ = plotfield(self, animation=animation, show_time=show_time, domain=domain, depth_level=depth_level,
                                 projection=projection, land=land, vmin=vmin, vmax=vmax, savefile=savefile, **kwargs)
        if plt:
            plt.show()

    def add_periodic_halo(self, zonal, meridional, halosize=5, data=None):
        """Add a 'halo' to all Fields in a FieldSet, through extending the Field (and lon/lat)
        by copying a small portion of the field on one side of the domain to the other.
        Before adding a periodic halo to the Field, it has to be added to the Grid on which the Field depends

        See `this tutorial <https://nbviewer.jupyter.org/github/OceanParcels/parcels/blob/master/parcels/examples/tutorial_periodic_boundaries.ipynb>`_
        for a detailed explanation on how to set up periodic boundaries

        :param zonal: Create a halo in zonal direction (boolean)
        :param meridional: Create a halo in meridional direction (boolean)
        :param halosize: size of the halo (in grid points). Default is 5 grid points
        :param data: if data is not None, the periodic halo will be achieved on data instead of self.data and data will be returned
        """
        dataNone = not isinstance(data, (np.ndarray, da.core.Array))
        if self.grid.defer_load and dataNone:
            return
        data = self.data if dataNone else data
        lib = np if isinstance(data, np.ndarray) else da
        if zonal:
            if len(data.shape) == 3:
                data = lib.concatenate((data[:, :, -halosize:], data,
                                       data[:, :, 0:halosize]), axis=len(data.shape)-1)
                assert data.shape[2] == self.grid.xdim, "Third dim must be x."
            else:
                data = lib.concatenate((data[:, :, :, -halosize:], data,
                                       data[:, :, :, 0:halosize]), axis=len(data.shape) - 1)
                assert data.shape[3] == self.grid.xdim, "Fourth dim must be x."
            self.lon = self.grid.lon
            self.lat = self.grid.lat
        if meridional:
            if len(data.shape) == 3:
                data = lib.concatenate((data[:, -halosize:, :], data,
                                       data[:, 0:halosize, :]), axis=len(data.shape)-2)
                assert data.shape[1] == self.grid.ydim, "Second dim must be y."
            else:
                data = lib.concatenate((data[:, :, -halosize:, :], data,
                                       data[:, :, 0:halosize, :]), axis=len(data.shape) - 2)
                assert data.shape[2] == self.grid.ydim, "Third dim must be y."
            self.lat = self.grid.lat
        if dataNone:
            self.data = data
        else:
            return data

    def write(self, filename, varname=None):
        """Write a :class:`Field` to a netcdf file

        :param filename: Basename of the file
        :param varname: Name of the field, to be appended to the filename"""
        filepath = str(Path('%s%s.nc' % (filename, self.name)))
        if varname is None:
            varname = self.name
        # Derive name of 'depth' variable for NEMO convention
        vname_depth = 'depth%s' % self.name.lower()

        # Create DataArray objects for file I/O
        if self.grid.gtype == GridCode.RectilinearZGrid:
            nav_lon = xr.DataArray(self.grid.lon + np.zeros((self.grid.ydim, self.grid.xdim), dtype=np.float32),
                                   coords=[('y', self.grid.lat), ('x', self.grid.lon)])
            nav_lat = xr.DataArray(self.grid.lat.reshape(self.grid.ydim, 1) + np.zeros(self.grid.xdim, dtype=np.float32),
                                   coords=[('y', self.grid.lat), ('x', self.grid.lon)])
        elif self.grid.gtype == GridCode.CurvilinearZGrid:
            nav_lon = xr.DataArray(self.grid.lon, coords=[('y', range(self.grid.ydim)),
                                                          ('x', range(self.grid.xdim))])
            nav_lat = xr.DataArray(self.grid.lat, coords=[('y', range(self.grid.ydim)),
                                                          ('x', range(self.grid.xdim))])
        else:
            raise NotImplementedError('Field.write only implemented for RectilinearZGrid and CurvilinearZGrid')

        attrs = {'units': 'seconds since ' + str(self.grid.time_origin)} if self.grid.time_origin.calendar else {}
        time_counter = xr.DataArray(self.grid.time,
                                    dims=['time_counter'],
                                    attrs=attrs)
        vardata = xr.DataArray(self.data.reshape((self.grid.tdim, self.grid.zdim, self.grid.ydim, self.grid.xdim)),
                               dims=['time_counter', vname_depth, 'y', 'x'])
        # Create xarray Dataset and output to netCDF format
        attrs = {'parcels_mesh': self.grid.mesh}
        dset = xr.Dataset({varname: vardata}, coords={'nav_lon': nav_lon,
                                                      'nav_lat': nav_lat,
                                                      'time_counter': time_counter,
                                                      vname_depth: self.grid.depth}, attrs=attrs)
        dset.to_netcdf(filepath, unlimited_dims='time_counter')

    def rescale_and_set_minmax(self, data):
        data[np.isnan(data)] = 0
        if self._scaling_factor:
            data *= self._scaling_factor
        if self.vmin is not None:
            data[data < self.vmin] = 0
        if self.vmax is not None:
            data[data > self.vmax] = 0
        return data

    def data_concatenate(self, data, data_to_concat, tindex):
        if data[tindex] is not None:
            if isinstance(data, np.ndarray):
                data[tindex] = None
            elif isinstance(data, list):
                del data[tindex]
        lib = np if isinstance(data, np.ndarray) else da
        if tindex == 0:
            data = lib.concatenate([data_to_concat, data[tindex+1:, :]], axis=0)
        elif tindex == 1:
            data = lib.concatenate([data[:tindex, :], data_to_concat], axis=0)
        else:
            raise ValueError("data_concatenate is used for computeTimeChunk, with tindex in [0, 1]")
        return data

    def advancetime(self, field_new, advanceForward):
        if isinstance(self.data) is not isinstance(field_new):
            logger.warning("[Field.advancetime] New field data and persistent field data have different types - time advance not possible.")
            return
        lib = np if isinstance(self.data, np.ndarray) else da
        if advanceForward == 1:  # forward in time, so appending at end
            self.data = lib.concatenate((self.data[1:, :, :], field_new.data[:, :, :]), 0)
            self.time = self.grid.time
        else:  # backward in time, so prepending at start
            self.data = lib.concatenate((field_new.data[:, :, :], self.data[:-1, :, :]), 0)
            self.time = self.grid.time

    def computeTimeChunk(self, data, tindex):
        g = self.grid
        timestamp = self.timestamps
        if timestamp is not None:
            summedlen = np.cumsum([len(ls) for ls in self.timestamps])
            if g.ti + tindex >= summedlen[-1]:
                ti = g.ti + tindex - summedlen[-1]
            else:
                ti = g.ti + tindex
            timestamp = self.timestamps[np.where(ti < summedlen)[0][0]]

        rechunk_callback_fields = self.chunk_setup if isinstance(tindex, list) else None
        filebuffer = self._field_fb_class(self.dataFiles[g.ti + tindex], self.dimensions, self.indices,
                                          netcdf_engine=self.netcdf_engine, timestamp=timestamp,
                                          interp_method=self.interp_method,
                                          data_full_zdim=self.data_full_zdim,
                                          chunksize=self.chunksize,
                                          rechunk_callback_fields=rechunk_callback_fields,
                                          chunkdims_name_map=self.netcdf_chunkdims_name_map)
        filebuffer.__enter__()
        time_data = filebuffer.time
        time_data = g.time_origin.reltime(time_data)
        filebuffer.ti = (time_data <= g.time[tindex]).argmin() - 1
        if self.netcdf_engine != 'xarray':
            filebuffer.name = filebuffer.parse_name(self.filebuffername)
        buffer_data = filebuffer.data
        lib = np if isinstance(buffer_data, np.ndarray) else da
        if len(buffer_data.shape) == 2:
            buffer_data = lib.reshape(buffer_data, sum(((1, 1), buffer_data.shape), ()))
        elif len(buffer_data.shape) == 3 and g.zdim > 1:
            buffer_data = lib.reshape(buffer_data, sum(((1, ), buffer_data.shape), ()))
        elif len(buffer_data.shape) == 3:
            buffer_data = lib.reshape(buffer_data, sum(((buffer_data.shape[0], 1, ), buffer_data.shape[1:]), ()))
        data = self.data_concatenate(data, buffer_data, tindex)
        self.filebuffers[tindex] = filebuffer
        return data

    def __add__(self, field):
        if isinstance(self, Field) and isinstance(field, Field):
            return SummedField('_SummedField', [self, field])
        elif isinstance(field, SummedField):
            assert isinstance(self, type(field[0])), 'Fields in a SummedField should be either all scalars or all vectors'
            field.insert(0, self)
            return field


class VectorField(object):
    """Class VectorField stores 2 or 3 fields which defines together a vector field.
    This enables to interpolate them as one single vector field in the kernels. (Python)

    :param name: Name of the vector field
    :param U: field defining the zonal component
    :param V: field defining the meridional component
    :param W: field defining the vertical component (default: None)
    """
    def __init__(self, name, U, V, W=None):
        self.name = name
        self.U = U
        self.V = V
        self.W = W
        if W is not None:
            self.numba_field = NumbaVectorField3D(
                name, U.numba_field, V.numba_field, W.numba_field)
        else:
            self.numba_field = NumbaVectorField2D(name, U.numba_field, V.numba_field)
        self.vector_type = '3D' if W else '2D'
        self.gridindexingtype = U.gridindexingtype
        if self.U.interp_method == 'cgrid_velocity':
            assert self.V.interp_method == 'cgrid_velocity', (
                'Interpolation methods of U and V are not the same.')
            assert self._check_grid_dimensions(U.grid, V.grid), (
                'Dimensions of U and V are not the same.')
            if self.vector_type == '3D':
                assert self.W.interp_method == 'cgrid_velocity', (
                    'Interpolation methods of U and W are not the same.')
                assert self._check_grid_dimensions(U.grid, W.grid), (
                    'Dimensions of U and W are not the same.')

    @staticmethod
    def _check_grid_dimensions(grid1, grid2):
        return (np.allclose(grid1.lon, grid2.lon) and np.allclose(grid1.lat, grid2.lat)
                and np.allclose(grid1.depth, grid2.depth) and np.allclose(grid1.time_full, grid2.time_full))

    def __getitem__(self, key):
        if _isParticle(key):
            return self.numba_field.eval(key.time, key.depth, key.lat, key.lon, key)
        else:
            return self.numba_field.eval(*key)


class DeferredArray():
    """Class used for throwing error when Field.data is not read in deferred loading mode

    TODO: Doesn't work.
    """
    data_shape = ()

    def __init__(self):
        self.data_shape = (1,)

    def compute_shape(self, xdim, ydim, zdim, tdim, tslices):
        if zdim == 1 and tdim == 1:
            self.data_shape = (tslices, 1, ydim, xdim)
        elif zdim > 1 or tdim > 1:
            if zdim > 1:
                self.data_shape = (1, zdim, ydim, xdim)
            else:
                self.data_shape = (max(tdim, tslices), 1, ydim, xdim)
        else:
            self.data_shape = (tdim, zdim, ydim, xdim)
        return self.data_shape

    def __getitem__(self, key):
        raise RuntimeError("Field is in deferred_load mode, so can't be accessed. Use .computeTimeChunk() method to force loading of data")


class SummedField(list):
    """TODO: Doesn't work at the moment.

    Class SummedField is a list of Fields over which Field interpolation
    is summed. This can e.g. be used when combining multiple flow fields,
    where the total flow is the sum of all the individual flows.
    Note that the individual Fields can be on different Grids.
    Also note that, since SummedFields are lists, the individual Fields can
    still be queried through their list index (e.g. SummedField[1]).
    SummedField is composed of either Fields or VectorFields.

    See `here <https://nbviewer.jupyter.org/github/OceanParcels/parcels/blob/master/parcels/examples/tutorial_SummedFields.ipynb>`_
    for a detailed tutorial

    :param name: Name of the SummedField
    :param F: List of fields. F can be a scalar Field, a VectorField, or the zonal component (U) of the VectorField
    :param V: List of fields defining the meridional component of a VectorField, if F is the zonal component. (default: None)
    :param W: List of fields defining the vertical component of a VectorField, if F and V are the zonal and meridional components (default: None)
    """

    def __init__(self, name, F, V=None, W=None):
        if V is None:
            if isinstance(F[0], VectorField):
                vector_type = F[0].vector_type
            for Fi in F:
                assert isinstance(Fi, Field) or (isinstance(Fi, VectorField) and Fi.vector_type == vector_type), 'Components of a SummedField must be Field or VectorField'
                self.append(Fi)
        elif W is None:
            for (i, Fi, Vi) in zip(range(len(F)), F, V):
                assert isinstance(Fi, Field) and isinstance(Vi, Field), \
                    'F, and V components of a SummedField must be Field'
                self.append(VectorField(name+'_%d' % i, Fi, Vi))
        else:
            for (i, Fi, Vi, Wi) in zip(range(len(F)), F, V, W):
                assert isinstance(Fi, Field) and isinstance(Vi, Field) and isinstance(Wi, Field), \
                    'F, V and W components of a SummedField must be Field'
                self.append(VectorField(name+'_%d' % i, Fi, Vi, Wi))
        self.name = name

    def __getitem__(self, key):
        if isinstance(key, int):
            return list.__getitem__(self, key)
        else:
            vals = []
            val = None
            for iField in range(len(self)):
                if _isParticle(key):
                    val = list.__getitem__(self, iField).eval(key.time, key.depth, key.lat, key.lon, particle=None)
                else:
                    val = list.__getitem__(self, iField).eval(*key)
                vals.append(val)
            return tuple(np.sum(vals, 0)) if isinstance(val, tuple) else np.sum(vals)

    def __add__(self, field):
        if isinstance(field, Field):
            assert isinstance(self[0], type(field)), 'Fields in a SummedField should be either all scalars or all vectors'
            self.append(field)
        elif isinstance(field, SummedField):
            assert isinstance(self[0], type(field[0])), 'Fields in a SummedField should be either all scalars or all vectors'
            for fld in field:
                self.append(fld)
        return self


class NestedField(list):
    """TODO: Doesn't work

    Class NestedField is a list of Fields from which the first one to be not declared out-of-boundaries
    at particle position is interpolated. This induces that the order of the fields in the list matters.
    Each one it its turn, a field is interpolated: if the interpolation succeeds or if an error other
    than `ErrorOutOfBounds` is thrown, the function is stopped. Otherwise, next field is interpolated.
    NestedField returns an `ErrorOutOfBounds` only if last field is as well out of boundaries.
    NestedField is composed of either Fields or VectorFields.

    See `here <https://nbviewer.jupyter.org/github/OceanParcels/parcels/blob/master/parcels/examples/tutorial_NestedFields.ipynb>`_
    for a detailed tutorial

    :param name: Name of the NestedField
    :param F: List of fields (order matters). F can be a scalar Field, a VectorField, or the zonal component (U) of the VectorField
    :param V: List of fields defining the meridional component of a VectorField, if F is the zonal component. (default: None)
    :param W: List of fields defining the vertical component of a VectorField, if F and V are the zonal and meridional components (default: None)
    """

    def __init__(self, name, F, V=None, W=None):
        if V is None:
            if isinstance(F[0], VectorField):
                vector_type = F[0].vector_type
            for Fi in F:
                assert isinstance(Fi, Field) or (isinstance(Fi, VectorField) and Fi.vector_type == vector_type), 'Components of a NestedField must be Field or VectorField'
                self.append(Fi)
        elif W is None:
            for (i, Fi, Vi) in zip(range(len(F)), F, V):
                assert isinstance(Fi, Field) and isinstance(Vi, Field), \
                    'F, and V components of a NestedField must be Field'
                self.append(VectorField(name+'_%d' % i, Fi, Vi))
        else:
            for (i, Fi, Vi, Wi) in zip(range(len(F)), F, V, W):
                assert isinstance(Fi, Field) and isinstance(Vi, Field) and isinstance(Wi, Field), \
                    'F, V and W components of a NestedField must be Field'
                self.append(VectorField(name+'_%d' % i, Fi, Vi, Wi))
        self.name = name

    def __getitem__(self, key):
        if isinstance(key, int):
            return list.__getitem__(self, key)
        else:
            for iField in range(len(self)):
                try:
                    if _isParticle(key):
                        val = list.__getitem__(self, iField).eval(key.time, key.depth, key.lat, key.lon, particle=None)
                    else:
                        val = list.__getitem__(self, iField).eval(*key)
                    break
                except (FieldOutOfBoundError, FieldSamplingError):
                    if iField == len(self)-1:
                        raise
                    else:
                        pass
            return val
