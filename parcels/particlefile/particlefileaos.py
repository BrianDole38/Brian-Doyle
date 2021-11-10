"""Module controlling the writing of ParticleSets to NetCDF file"""
import os
from glob import glob
import numpy as np

try:
    from mpi4py import MPI
except:
    MPI = None

from parcels.particlefile.baseparticlefile import BaseParticleFile

__all__ = ['ParticleFileAOS']


class ParticleFileAOS(BaseParticleFile):
    """
    Initialise trajectory output.

    :param name: Basename of the output file
    :param particleset: ParticleSet to output
    :param outputdt: Interval which dictates the update frequency of file output
                     while ParticleFile is given as an argument of ParticleSet.execute()
                     It is either a timedelta object or a positive double.
    :param write_ondelete: Boolean to write particle data only when they are deleted. Default is False
    :param convert_at_end: Boolean to convert npy files to netcdf at end of run. Default is True
    :param tempwritedir: directories to write temporary files to during executing.
                     Default is out-XXXXXX where Xs are random capitals. Files for individual
                     processors are written to subdirectories 0, 1, 2 etc under tempwritedir
    :param pset_info: dictionary of info on the ParticleSet, stored in tempwritedir/XX/pset_info.npy,
                     used to create NetCDF file from npy-files.
    """

    def __init__(self, name, particleset, outputdt=np.infty, write_ondelete=False, convert_at_end=True,
                 tempwritedir=None, pset_info=None):
        """
        ParticleFileAOS - Constructor
        :param name: Basename of the output file
        :param particleset: ParticleSet to output
        :param outputdt: Interval which dictates the update frequency of file output
                         while ParticleFile is given as an argument of ParticleSet.execute()
                         It is either a timedelta object or a positive double.
        :param write_ondelete: Boolean to write particle data only when they are deleted. Default is False
        :param convert_at_end: Boolean to convert npy files to netcdf at end of run. Default is True
        :param tempwritedir: directories to write temporary files to during executing.
                         Default is out-XXXXXX where Xs are random capitals. Files for individual
                         processors are written to subdirectories 0, 1, 2 etc under tempwritedir
        :param pset_info: dictionary of info on the ParticleSet, stored in tempwritedir/XX/pset_info.npy,
                         used to create NetCDF file from npy-files.
        """
        super(ParticleFileAOS, self).__init__(name=name, particleset=particleset, outputdt=outputdt,
                                              write_ondelete=write_ondelete, convert_at_end=convert_at_end,
                                              tempwritedir=tempwritedir, pset_info=pset_info)

    def __del__(self):
        """
        ParticleFileAOS - Destructor
        """
        super(ParticleFileAOS, self).__del__()

    def _reserved_var_names(self):
        """
        :returns the reserved dimension names not to be written just once.
        """
        return ['time', 'lat', 'lon', 'depth', 'id']

    def _create_trajectory_records(self, coords):
        """
        This function creates the NetCDF record of the ParticleSet inside the output NetCDF file
        :arg coords: tuple of dictionary keys for # entities ("traj(ectories)") and timesteps ("obs(ervations)")
        """
        super(ParticleFileAOS, self)._create_trajectory_records(coords=coords)

    def get_pset_info_attributes(self):
        """
        :returns the main attributes of the pset_info.npy file.

        Attention:
        For ParticleSet structures other than SoA, and structures where ID != index, this has to be overridden.
        """
        attributes = ['name', 'var_names', 'var_names_once', 'time_origin', 'lonlatdepth_dtype',
                      'file_list', 'file_list_once', 'parcels_mesh', 'metadata']
        return attributes

    def read_from_npy(self, file_list, n_timesteps, var):
        """
        Read NPY-files for one variable using a loop over all files.

        Attention:
        For ParticleSet structures other than SoA, and structures where ID != index, this has to be overridden.

        :param file_list: List that  contains all file names in the output directory
        :param n_timesteps: Dictionary with (for each particle) number of time steps that were written in out directory
        :param var: name of the variable to read
        :returns data dictionary of time instances to be written
        """
        max_timesteps = max(n_timesteps.values()) if n_timesteps.keys() else 0
        data = np.nan * np.zeros((len(n_timesteps), max_timesteps))  # there is the issue that this array should be structured - we need to define its type here!
        time_index = np.zeros(len(n_timesteps), dtype=np.int32)
        id_index = {}
        count = 0
        for i in sorted(n_timesteps.keys()):
            id_index[i] = count
            count += 1

        for npyfile in file_list:
            try:
                data_dict = np.load(npyfile, allow_pickle=True).item()
            except NameError:
                raise RuntimeError('Cannot combine npy files into netcdf file because your ParticleFile is '
                                   'still open on interpreter shutdown.\nYou can use '
                                   '"parcels_convert_npydir_to_netcdf %s" to convert these to '
                                   'a NetCDF file yourself.\nTo avoid this error, make sure you '
                                   'close() your ParticleFile at the end of your script.' % self.tempwritedir)
            for ii, i in enumerate(data_dict["id"]):
                id_ind = id_index[i]
                t_ind = time_index[id_ind] if 'once' not in file_list[0] else 0
                data[id_ind, t_ind] = data_dict[var][ii]
                time_index[id_ind] = time_index[id_ind] + 1

        return data[time_index > 0, :]

    def export(self):
        """
        Exports outputs in temporary NPY-files to NetCDF file
        """
        if MPI:
            # The export can only start when all threads are done.
            MPI.COMM_WORLD.Barrier()
            if MPI.COMM_WORLD.Get_rank() > 0:
                return  # export only on process 0

        # Retrieve all temporary writing directories and sort them in numerical order
        temp_names = sorted(glob(os.path.join("%s" % self.tempwritedir_base, "*")),
                            key=lambda x: int(os.path.basename(x)))

        if len(temp_names) == 0:
            raise RuntimeError("No npy files found in %s" % self.tempwritedir_base)

        n_timesteps = {}
        global_file_list = []
        global_file_list_once = []
        for tempwritedir in temp_names:
            if os.path.exists(tempwritedir):
                pset_info_local = np.load(os.path.join(tempwritedir, 'pset_info.npy'), allow_pickle=True).item()
                for npyfile in pset_info_local['file_list']:
                    tmp_dict = np.load(npyfile, allow_pickle=True).item()
                    for i in tmp_dict['id']:
                        if i in n_timesteps:
                            n_timesteps[i] += 1
                        else:
                            n_timesteps[i] = 1
                global_file_list += pset_info_local['file_list']
                if len(self.var_names_once) > 0:
                    global_file_list_once += pset_info_local['file_list_once']

        for var in self.var_names:
            data = self.read_from_npy(global_file_list, n_timesteps, var)
            if var == self.var_names[0]:
                self.open_netcdf_file(data.shape)
            varout = 'z' if var == 'depth' else var
            getattr(self, varout)[:, :] = data

        if len(self.var_names_once) > 0:
            n_timesteps_once = {}
            for i in n_timesteps:
                n_timesteps_once[i] = 1
            for var in self.var_names_once:
                getattr(self, var)[:] = self.read_from_npy(global_file_list_once, n_timesteps_once, var)

        self.close_netcdf_file()
