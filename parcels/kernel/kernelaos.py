import inspect
import math  # noqa
import random  # noqa
from ast import parse
from copy import deepcopy
from ctypes import byref
from ctypes import c_double
from ctypes import c_int
from os import path

import numpy as np
try:
    from mpi4py import MPI
except:
    MPI = None

from parcels.kernel.basekernel import BaseKernel
from parcels.kernel.benchmarkkernel import BaseBenchmarkKernel
from parcels.compilation.codegenerator import ObjectKernelGenerator as KernelGenerator
from parcels.compilation.codegenerator import ParticleObjectLoopGenerator
from parcels.field import NestedField
from parcels.field import SummedField
from parcels.field import VectorField
import parcels.rng as ParcelsRandom  # noqa
from parcels.tools.statuscodes import StateCode, OperationCode, ErrorCode  # noqa
from parcels.tools.statuscodes import recovery_map as recovery_base_map
from parcels.tools.loggers import logger


__all__ = ['KernelAOS', 'BenchmarkKernelAOS']


class KernelAOS(BaseBenchmarkKernel):
    """Kernel object that encapsulates auto-generated code.

    :arg fieldset: FieldSet object providing the field information
    :arg ptype: PType object for the kernel particle
    :param delete_cfiles: Boolean whether to delete the C-files after compilation in JIT mode (default is True)

    Note: A Kernel is either created from a compiled <function ...> object
    or the necessary information (funcname, funccode, funcvars) is provided.
    The py_ast argument may be derived from the code string, but for
    concatenation, the merged AST plus the new header definition is required.
    """

    def __init__(self, fieldset, ptype, pyfunc=None, funcname=None,
                 funccode=None, funcvars=None, py_ast=None, c_include="", delete_cfiles=True, **kwargs):
        """
        KernelAOS - Constructor
        :arg fieldset: the fieldset object of the host ParticleSet
        :arg ptype: the ParticleType of the host ParticleSet
        :arg pyfunc: the initial Python func or a concatenation of BaseKernel's hosting the kernel code
        :arg funcname: None, if :arg pyfunc is a concatenated kernel or python function; otherwise, name of the custom function
        :arg funccode: None, if :arg pyfunc is a concatenated kernel or python function; otherwise, string of code of the custom function
        :arg funcvars: None, if :arg pyfunc is a concatenated kernel or python function; otherwise, variables of the custom function
        :arg py_ast: a parsed hierarchy of generated functions
        :arg c_include: added C include functions for generation or interpretation of the custom function
        :arg delete_cfiles: boolean, indicating whether the written C source files are deleted on destruction
        """
        super(KernelAOS, self).__init__(fieldset=fieldset, ptype=ptype, pyfunc=pyfunc, funcname=funcname, funccode=funccode, py_ast=py_ast, funcvars=funcvars, c_include=c_include, delete_cfiles=delete_cfiles, **kwargs)

        # Derive meta information from pyfunc, if not given
        self.check_fieldsets_in_kernels(pyfunc)

        if funcvars is not None:
            self.funcvars = funcvars
        elif hasattr(pyfunc, '__code__'):
            self.funcvars = list(pyfunc.__code__.co_varnames)
        else:
            self.funcvars = None
        self.funccode = funccode or inspect.getsource(pyfunc.__code__)
        # Parse AST if it is not provided explicitly
        self.py_ast = py_ast or parse(BaseKernel.fix_indentation(self.funccode)).body[0]
        if pyfunc is None:
            # Extract user context by inspecting the call stack
            stack = inspect.stack()
            try:
                user_ctx = stack[-1][0].f_globals
                user_ctx['math'] = globals()['math']
                user_ctx['ParcelsRandom'] = globals()['ParcelsRandom']
                user_ctx['random'] = globals()['random']
                user_ctx['StateCode'] = globals()['StateCode']
                user_ctx['OperationCode'] = globals()['OperationCode']
                user_ctx['ErrorCode'] = globals()['ErrorCode']
            except:
                logger.warning("Could not access user context when merging kernels")
                user_ctx = globals()
            finally:
                del stack  # Remove cyclic references
            # Compile and generate Python function from AST
            py_mod = parse("")
            py_mod.body = [self.py_ast]
            exec(compile(py_mod, "<ast>", "exec"), user_ctx)
            self._pyfunc = user_ctx[self.funcname]
        else:
            self._pyfunc = pyfunc

        numkernelargs = self.check_kernel_signature_on_version()
        assert numkernelargs == 3, \
            'Since Parcels v2.0, kernels do only take 3 arguments: particle, fieldset, time !! AND !! Argument order in field interpolation is time, depth, lat, lon.'

        self.name = "%s%s" % (self._ptype.name, self.funcname)

        # Generate the kernel function and add the outer loop
        if self.ptype.uses_jit:
            kernelgen = KernelGenerator(self.fieldset, self.ptype)
            kernel_ccode = kernelgen.generate(deepcopy(self.py_ast), self.funcvars)
            self.field_args = kernelgen.field_args
            self.vector_field_args = kernelgen.vector_field_args
            for f in self.vector_field_args.values():
                Wname = f.W.ccode_name if f.W else 'not_defined'
                for sF_name, sF_component in zip([f.U.ccode_name, f.V.ccode_name, Wname], ['U', 'V', 'W']):
                    if sF_name not in self.field_args:
                        if sF_name != 'not_defined':
                            self.field_args[sF_name] = getattr(f, sF_component)
            self.const_args = kernelgen.const_args
            loopgen = ParticleObjectLoopGenerator(self.fieldset, self.ptype)
            if path.isfile(c_include):
                with open(c_include, 'r') as f:
                    c_include_str = f.read()
            else:
                c_include_str = c_include
            self.ccode = loopgen.generate(self.funcname, self.field_args, self.const_args,
                                          kernel_ccode, c_include_str)

            src_file_or_files, self.lib_file, self.log_file = self.get_kernel_compile_files()
            self.dyn_srcs = src_file_or_files

    def __del__(self):
        """
        KernelAOS - Destructor
        """
        # Clean-up the in-memory dynamic linked libraries.
        # This is not really necessary, as these programs are not that large, but with the new random
        # naming scheme which is required on Windows OS'es to deal with updates to a Parcels' kernel.)
        super(KernelAOS, self).__del__()

    def generate_sources(self):
        """
        Generates and compiles a kernel with its source(s)
        """
        pass

    def __add__(self, kernel):
        """
        Adds (via '+') one kernel to another. In the expression
        k = a + b
        this function covers the case where at least 'a' is a BaseKernel.
        :arg kernel: BaseKernel or python function object to be merged (i.e. added)
        """
        mkernel = kernel  # do this to avoid rewriting the object put in as parameter
        if not isinstance(mkernel, BaseKernel):
            mkernel = KernelAOS(self.fieldset, self.ptype, pyfunc=kernel)
        elif not isinstance(mkernel, KernelAOS) and kernel.pyfunc is not None:
            mkernel = KernelAOS(self.fieldset, self.ptype, pyfunc=mkernel.pyfunc)
        return self.merge(mkernel, KernelAOS)

    def __radd__(self, kernel):
        """
        Adds (via '+') one kernel to another. In the expression
        k = a + b
        this function covers the case where at least 'b' is a BaseKernel.
        :arg kernel: BaseKernel or python function object to be merged (i.e. added)
        """
        mkernel = kernel  # do this to avoid rewriting the object put in as parameter
        if not isinstance(mkernel, BaseKernel):
            mkernel = KernelAOS(self.fieldset, self.ptype, pyfunc=kernel)
        elif not isinstance(mkernel, KernelAOS) and kernel.pyfunc is not None:
            mkernel = KernelAOS(self.fieldset, self.ptype, pyfunc=mkernel.pyfunc)
        return mkernel.merge(self, KernelAOS)

    def execute_jit(self, pset, endtime, dt):
        """
        Invokes JIT engine to perform the core update loop
        :arg pset: particle set to calculate
        :arg endtime: timestamp to calculate
        :arg dt: delta-t to be calculated
        """
        self.load_fieldset_jit(pset)

        fargs = []
        if self.field_args is not None:
            fargs += [byref(f.ctypes_struct) for f in self.field_args.values()]
        if self.const_args is not None:
            fargs += [c_double(f) for f in self.const_args.values()]

        pdata = pset.ctypes_struct
        if len(fargs) > 0:
            self._function(c_int(len(pset)), pdata, c_double(endtime), c_double(dt), *fargs)
        else:
            self._function(c_int(len(pset)), pdata, c_double(endtime), c_double(dt))

    def execute_python(self, pset, endtime, dt):
        """
        Performs the core update loop via Python
        :arg pset: particle set to calculate
        :arg endtime: timestamp to calculate
        :arg dt: delta-t to be calculated
        """
        # sign of dt: { [0, 1]: forward simulation; -1: backward simulation }
        sign_dt = np.sign(dt)

        analytical = False
        if 'AdvectionAnalytical' in self._pyfunc.__name__:
            analytical = True
            if not np.isinf(dt):
                logger.warning_once('dt is not used in AnalyticalAdvection, so is set to np.inf')
            dt = np.inf

        if self.fieldset is not None:
            for f in self.fieldset.get_fields():
                if type(f) in [VectorField, NestedField, SummedField]:
                    continue
                f.data = np.array(f.data)

        target_time = endtime
        for p in pset:
            self.evaluate_particle(p, target_time, sign_dt, dt, analytical=analytical)

    def remove_deleted(self, pset, output_file, endtime):
        """
        Utility to remove all particles that signalled deletion

        This version is generally applicable to all structures and collections
        :arg pset: host ParticleSet
        :arg output_file: instance of ParticleFile object of the host ParticleSet where deleted objects are to be written to on deletion
        :arg endtime: timestamp at which the particles are to be deleted
        """
        indices = [i for i, p in enumerate(pset) if p.state == OperationCode.Delete]
        if len(indices) > 0 and output_file is not None:
            output_file.write(pset, endtime, deleted_only=indices)
        pset.remove_indices(indices)

    def execute(self, pset, endtime, dt, recovery=None, output_file=None, execute_once=False):
        """
        Execute this Kernel over a ParticleSet for several timesteps
        :arg pset: host ParticleSet
        :arg endtime: endtime of this overall kernel evaluation step
        :arg dt: computational integration timestep
        :arg recovery: dict of recovery code -> recovery function
        :arg output_file: instance of ParticleFile object of the host ParticleSet where deleted objects are to be written to on deletion
        :arg execute_once: boolean, telling if to execute once (True) or computing the kernel iteratively
        """
        for p in pset:
            p.reset_state()

        if abs(dt) < 1e-6 and not execute_once:
            logger.warning_once("'dt' is too small, causing numerical accuracy limit problems. Please chose a higher 'dt' and rather scale the 'time' axis of the field accordingly. (related issue #762)")

        if recovery is None:
            recovery = {}
        elif ErrorCode.ErrorOutOfBounds in recovery and ErrorCode.ErrorThroughSurface not in recovery:
            recovery[ErrorCode.ErrorThroughSurface] = recovery[ErrorCode.ErrorOutOfBounds]
        recovery_map = recovery_base_map.copy()
        recovery_map.update(recovery)

        if pset.fieldset is not None:
            for g in pset.fieldset.gridset.grids:
                if len(g.load_chunk) > g.chunk_not_loaded:  # not the case if a field in not called in the kernel
                    g.load_chunk = np.where(g.load_chunk == g.chunk_loaded_touched,
                                            g.chunk_deprecated, g.load_chunk)

        # Execute the kernel over the particle set
        if self.ptype.uses_jit:
            self.execute_jit(pset, endtime, dt)
        else:
            self.execute_python(pset, endtime, dt)

        # Remove all particles that signalled deletion
        self.remove_deleted(pset, output_file=output_file, endtime=endtime)

        # Identify particles that threw errors
        error_particles = [p for p in pset if p.state not in [StateCode.Success, StateCode.Evaluate]]

        while len(error_particles) > 0:
            # Apply recovery kernel
            for p in error_particles:
                if p.state == OperationCode.StopExecution:
                    return
                if p.state == OperationCode.Repeat:
                    p.reset_state()
                elif p.state == OperationCode.Delete:
                    pass
                elif p.state in recovery_map:
                    recovery_kernel = recovery_map[p.state]
                    p.set_state(StateCode.Success)
                    recovery_kernel(p, self.fieldset, p.time)
                    if p.isComputed():
                        p.reset_state()
                else:
                    logger.warning_once('Deleting particle {} because of non-recoverable error'.format(p.id))
                    p.delete()

            # Remove all particles that signalled deletion
            self.remove_deleted(pset, output_file=output_file, endtime=endtime)

            # Execute core loop again to continue interrupted particles
            if self.ptype.uses_jit:
                self.execute_jit(pset, endtime, dt)
            else:
                self.execute_python(pset, endtime, dt)

            error_particles = [p for p in pset if p.state not in [StateCode.Success, StateCode.Evaluate]]


class BenchmarkKernelAOS(KernelAOS):

    def __init__(self, fieldset, ptype, pyfunc=None, funcname=None,
                 funccode=None, py_ast=None, funcvars=None, c_include="", delete_cfiles=True):
        super(BenchmarkKernelAOS, self).__init__(fieldset=fieldset, ptype=ptype, pyfunc=pyfunc, funcname=funcname, funccode=funccode, py_ast=py_ast, funcvars=funcvars, c_include=c_include, delete_cfiles=delete_cfiles, use_benchmark=True)

    def __del__(self):
        super(BenchmarkKernelAOS, self).__del__()

    def __add__(self, kernel):
        """
        Adds (via '+') one kernel to another. In the expression
        k = a + b
        this function covers the case where at least 'a' is a BaseKernel.
        :arg kernel: BaseKernel or python function object to be merged (i.e. added)
        """
        mkernel = kernel  # do this to avoid rewriting the object put in as parameter
        if not isinstance(mkernel, BaseBenchmarkKernel):
            mkernel = BenchmarkKernelAOS(self.fieldset, self.ptype, pyfunc=kernel)
        elif not isinstance(mkernel, BenchmarkKernelAOS) and kernel.pyfunc is not None:
            mkernel = BenchmarkKernelAOS(self.fieldset, self.ptype, pyfunc=mkernel.pyfunc)
        return self.merge(mkernel, BenchmarkKernelAOS)

    def __radd__(self, kernel):
        """
        Adds (via '+') one kernel to another. In the expression
        k = a + b
        this function covers the case where at least 'b' is a BaseKernel.
        :arg kernel: BaseKernel or python function object to be merged (i.e. added)
        """
        mkernel = kernel  # do this to avoid rewriting the object put in as parameter
        if not isinstance(mkernel, BaseBenchmarkKernel):
            mkernel = BenchmarkKernelAOS(self.fieldset, self.ptype, pyfunc=kernel)
        elif not isinstance(mkernel, BenchmarkKernelAOS) and kernel.pyfunc is not None:
            mkernel = BenchmarkKernelAOS(self.fieldset, self.ptype, pyfunc=mkernel.pyfunc)
        return mkernel.merge(self, BenchmarkKernelAOS)

    def execute_jit(self, pset, endtime, dt):
        """
        Invokes JIT engine to perform the core update loop
        :arg pset: particle set to calculate
        :arg endtime: timestamp to calculate
        :arg dt: delta-t to be calculated
        """
        if not self.perform_benchmark:
            super(BenchmarkKernelAOS, self).execute_jit(pset, endtime, dt)
        self.load_fieldset_jit(pset)

        self._compute_timings.start_timing()
        fargs = []
        if self.field_args is not None:
            fargs += [byref(f.ctypes_struct) for f in self.field_args.values()]
        if self.const_args is not None:
            fargs += [c_double(f) for f in self.const_args.values()]

        pdata = pset.ctypes_struct
        result = None
        if len(fargs) > 0:
            result = self._function(c_int(len(pset)), pdata, c_double(endtime), c_double(dt), *fargs)
        else:
            result = self._function(c_int(len(pset)), pdata, c_double(endtime), c_double(dt))
        self._compute_timings.stop_timing()
        self._compute_timings.accumulate_timing()

        self._io_timings.advance_iteration()
        self._mem_io_timings.advance_iteration()
        self._compute_timings.advance_iteration()
        return result

    def execute_python(self, pset, endtime, dt):
        """
        Performs the core update loop via Python
        :arg pset: particle set to calculate
        :arg endtime: timestamp to calculate
        :arg dt: delta-t to be calculated
        """
        if not self.perform_benchmark:
            super(BenchmarkKernelAOS, self).execute_python(pset, endtime, dt)
        # sign of dt: { [0, 1]: forward simulation; -1: backward simulation }
        sign_dt = np.sign(dt)

        analytical = False
        if 'AdvectionAnalytical' in self._pyfunc.__name__:
            analytical = True
            if not np.isinf(dt):
                logger.warning_once('dt is not used in AnalyticalAdvection, so is set to np.inf')
            dt = np.inf

        if self.fieldset is not None:
            for f in self.fieldset.get_fields():
                if type(f) in [VectorField, NestedField, SummedField]:
                    continue
                self._io_timings.start_timing()
                loaded_data = f.data
                self._io_timings.stop_timing()
                self._io_timings.accumulate_timing()
                self._mem_io_timings.start_timing()
                f.data = np.array(loaded_data)
                self._mem_io_timings.stop_timing()
                self._mem_io_timings.accumulate_timing()

        self._compute_timings.start_timing()
        target_time = endtime
        for p in pset:
            self.evaluate_particle(p, target_time, sign_dt, dt, analytical=analytical)
        self._compute_timings.stop_timing()
        self._compute_timings.accumulate_timing()

        self._io_timings.advance_iteration()
        self._mem_io_timings.advance_iteration()
        self._compute_timings.advance_iteration()

    def execute(self, pset, endtime, dt, recovery=None, output_file=None, execute_once=False):
        """
        Execute this Kernel over a ParticleSet for several timesteps
        :arg pset: host ParticleSet
        :arg endtime: endtime of this overall kernel evaluation step
        :arg dt: computational integration timestep
        :arg recovery: dict of recovery code -> recovery function
        :arg output_file: instance of ParticleFile object of the host ParticleSet where deleted objects are to be written to on deletion
        :arg execute_once: boolean, telling if to execute once (True) or computing the kernel iteratively
        """
        self._mem_io_timings.start_timing()
        for p in pset:
            p.reset_state()
        self._mem_io_timings.stop_timing()
        self._mem_io_timings.accumulate_timing()

        if abs(dt) < 1e-6 and not execute_once:
            logger.warning_once("'dt' is too small, causing numerical accuracy limit problems. Please chose a higher 'dt' and rather scale the 'time' axis of the field accordingly. (related issue #762)")

        if recovery is None:
            recovery = {}
        elif ErrorCode.ErrorOutOfBounds in recovery and ErrorCode.ErrorThroughSurface not in recovery:
            recovery[ErrorCode.ErrorThroughSurface] = recovery[ErrorCode.ErrorOutOfBounds]
        recovery_map = recovery_base_map.copy()
        recovery_map.update(recovery)

        self._mem_io_timings.start_timing()
        if pset.fieldset is not None:
            for g in pset.fieldset.gridset.grids:
                if len(g.load_chunk) > g.chunk_not_loaded:  # not the case if a field in not called in the kernel
                    g.load_chunk = np.where(g.load_chunk == g.chunk_loaded_touched,
                                            g.chunk_deprecated, g.load_chunk)
        self._mem_io_timings.stop_timing()
        self._mem_io_timings.accumulate_timing()

        # Execute the kernel over the particle set
        if self.ptype.uses_jit:
            self.execute_jit(pset, endtime, dt)
        else:
            self.execute_python(pset, endtime, dt)

        # Remove all particles that signalled deletion
        self.benchmark_remove_deleted(pset, output_file=output_file, endtime=endtime)

        # Identify particles that threw errors
        self._mem_io_timings.start_timing()
        error_particles = [p for p in pset if p.state not in [StateCode.Success, StateCode.Evaluate]]
        self._mem_io_timings.stop_timing()
        self._mem_io_timings.accumulate_timing()

        while len(error_particles) > 0:
            # Apply recovery kernel
            for p in error_particles:
                if p.state == OperationCode.StopExecution:
                    return
                if p.state == OperationCode.Repeat:
                    p.reset_state()
                elif p.state == OperationCode.Delete:
                    pass
                elif p.state in recovery_map:
                    recovery_kernel = recovery_map[p.state]
                    p.set_state(StateCode.Success)
                    recovery_kernel(p, self.fieldset, p.time)
                    if p.isComputed():
                        p.reset_state()
                else:
                    logger.warning_once('Deleting particle {} because of non-recoverable error'.format(p.id))
                    p.delete()

            # Remove all particles that signalled deletion
            self.benchmark_remove_deleted(pset, output_file=output_file, endtime=endtime)

            # Execute core loop again to continue interrupted particles
            if self.ptype.uses_jit:
                self.execute_jit(pset, endtime, dt)
            else:
                self.execute_python(pset, endtime, dt)

            self._mem_io_timings.start_timing()
            error_particles = [p for p in pset if p.state not in [StateCode.Success, StateCode.Evaluate]]
            self._mem_io_timings.stop_timing()
            self._mem_io_timings.accumulate_timing()
