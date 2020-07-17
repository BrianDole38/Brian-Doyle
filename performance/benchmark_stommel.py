"""
Author: Dr. Christian Kehl
Date: 11-02-2020
"""

from parcels import AdvectionEE, AdvectionRK45, AdvectionRK4
from parcels import FieldSet, ScipyParticle, JITParticle, Variable, AdvectionRK4, ErrorCode
from parcels.particleset_benchmark import ParticleSet_Benchmark as ParticleSet
# from parcels.kernel_benchmark import Kernel_Benchmark as Kernel
from parcels.field import VectorField, NestedField, SummedField
# from parcels import plotTrajectoriesFile_loadedField
from datetime import timedelta as delta
import math
from argparse import ArgumentParser
import datetime
import numpy as np
import xarray as xr
# import pytest
import fnmatch
import gc
import os
import time as ostime
import matplotlib.pyplot as plt

from parcels import rng as random

import sys
try:
    from mpi4py import MPI
except:
    MPI = None
with_GC = False

pset = None


# ptype = {'scipy': ScipyParticle, 'jit': JITParticle}
method = {'RK4': AdvectionRK4, 'EE': AdvectionEE, 'RK45': AdvectionRK45}
global_t_0 = 0
Nparticle = int(math.pow(2,10)) # equals to Nparticle = 1024
#Nparticle = int(math.pow(2,11)) # equals to Nparticle = 2048
#Nparticle = int(math.pow(2,12)) # equals to Nparticle = 4096
#Nparticle = int(math.pow(2,13)) # equals to Nparticle = 8192
#Nparticle = int(math.pow(2,14)) # equals to Nparticle = 16384
#Nparticle = int(math.pow(2,15)) # equals to Nparticle = 32768
#Nparticle = int(math.pow(2,16)) # equals to Nparticle = 65536
#Nparticle = int(math.pow(2,17)) # equals to Nparticle = 131072
#Nparticle = int(math.pow(2,18)) # equals to Nparticle = 262144
#Nparticle = int(math.pow(2,19)) # equals to Nparticle = 524288

a = 10000 * 1e3
b = 10000 * 1e3
scalefac = 0.05  # to scale for physically meaningful velocities

def plot_internal(total_times = None, compute_times = None, io_times = None, memory_used = None, nparticles = None, imageFilePath = ""):
    if total_times is None:
        total_times = []
    if compute_times is None:
        compute_times = []
    if io_times is None:
        io_times = []
    if memory_used is None:
        memory_used = []
    if nparticles is None:
        nparticles = []
    plot_t = []
    plot_ct = []
    plot_iot = []
    plot_npart = []
    cum_t = 0
    cum_ct = 0
    cum_iot = 0
    t_scaler = 1. * 10./1.0
    npart_scaler = 1.0 / 1000.0
    for i in range(0, len(total_times)):
        plot_t.append( total_times[i]*t_scaler )
        cum_t += (total_times[i])

    for i in range(0, len(compute_times)):
        plot_ct.append(compute_times[i] * t_scaler)
        cum_ct += compute_times[i]
    for i in range(0, len(io_times)):
        plot_iot.append(io_times[i] * t_scaler)
        cum_iot += io_times[i]
    for i in range(0, len(nparticles)):
        plot_npart.append(nparticles[i] * npart_scaler)


    plot_mem = []
    if memory_used is not None:
        #mem_scaler = (1*10)/(1024*1024*1024)
        mem_scaler = 1 / (1024 * 1024 * 1024)
        for i in range(0, len(memory_used)):
            plot_mem.append(memory_used[i] * mem_scaler)

    do_iot_plot = True
    do_mem_plot = True
    do_npart_plot = True
    assert (len(plot_t) == len(plot_ct))
    # assert (len(plot_t) == len(plot_iot))
    if len(plot_t) != len(plot_iot):
        print("plot_t and plot_iot have different lengths ({} vs {})".format(len(plot_t), len(plot_iot)))
        do_iot_plot = False
    # assert (len(plot_t) == len(plot_mem))
    if len(plot_t) != len(plot_mem):
        print("plot_t and plot_mem have different lengths ({} vs {})".format(len(plot_t), len(plot_mem)))
        do_mem_plot = False
    # assert (len(plot_t) == len(plot_npart))
    if len(plot_t) != len(plot_npart):
        print("plot_t and plot_npart have different lengths ({} vs {})".format(len(plot_t), len(plot_npart)))
        do_npart_plot = False
    x = []
    for i in range(len(plot_t)):
        x.append(i)

    fig, ax = plt.subplots(1, 1, figsize=(21, 12))
    ax.plot(x, plot_t, 'o-', label="total time_spent [100ms]")
    ax.plot(x, plot_ct, 'o-', label="compute time_spent [100ms]")
    # == this is still the part that breaks - as they are on different time scales, possibly leave them out ? == #
    if do_iot_plot:
        ax.plot(x, plot_iot, 'o-', label="io time_spent [100ms]")
    if (memory_used is not None) and do_mem_plot:
        #ax.plot(x, plot_mem, 'x-', label="memory_used (cumulative) [100 MB]")
        ax.plot(x, plot_mem, 'x-', label="memory_used (cumulative) [1 GB]")
    if do_npart_plot:
        ax.plot(x, plot_npart, '-', label="sim. particles [# 1000]")
    plt.xlim([0, 730])
    plt.ylim([0, 120])
    plt.legend()
    ax.set_xlabel('iteration')
    plt.savefig(os.path.join(odir, imageFilePath), dpi=600, format='png')

    sys.stdout.write("cumulative total runtime: {}\n".format(cum_t))
    sys.stdout.write("cumulative compute time: {}\n".format(cum_ct))
    sys.stdout.write("cumulative I/O time: {}\n".format(cum_iot))

def DeleteParticle(particle, fieldset, time):
    particle.delete()

def RenewParticle(particle, fieldset, time):
    particle.lat = np.random.rand() * a
    particle.lon = np.random.rand() * b

def perIterGC():
    gc.collect()

def stommel_fieldset_from_numpy(xdim=200, ydim=200, periodic_wrap=False):
    """Simulate a periodic current along a western boundary, with significantly
    larger velocities along the western edge than the rest of the region

    The original test description can be found in: N. Fabbroni, 2009,
    Numerical Simulation of Passive tracers dispersion in the sea,
    Ph.D. dissertation, University of Bologna
    http://amsdottorato.unibo.it/1733/1/Fabbroni_Nicoletta_Tesi.pdf
    """

    # Coordinates of the test fieldset (on A-grid in deg)
    lon = np.linspace(0, a, xdim, dtype=np.float32)
    lat = np.linspace(0, b, ydim, dtype=np.float32)
    totime = ydim*24.0*60.0*60.0
    time = np.linspace(0., totime, ydim, dtype=np.float64)

    # Define arrays U (zonal), V (meridional), W (vertical) and P (sea
    # surface height) all on A-grid
    U = np.zeros((lon.size, lat.size, time.size), dtype=np.float32)
    V = np.zeros((lon.size, lat.size, time.size), dtype=np.float32)
    P = np.zeros((lon.size, lat.size, time.size), dtype=np.float32)

    beta = 2e-11
    r = 1/(11.6*86400)
    es = r/(beta*a)

    for i in range(lon.size):
        for j in range(lat.size):
            xi = lon[i] / a
            yi = lat[j] / b
            P[i, j, 0] = (1 - math.exp(-xi/es) - xi) * math.pi * np.sin(math.pi*yi)*scalefac
            U[i, j, 0] = -(1 - math.exp(-xi/es) - xi) * math.pi**2 * np.cos(math.pi*yi)*scalefac
            V[i, j, 0] = (math.exp(-xi/es)/es - 1) * math.pi * np.sin(math.pi*yi)*scalefac

    for t in range(1, time.size):
        for i in range(lon.size):
            for j in range(lat.size):
                P[i, j, t] = P[i, j - 1, t - 1]
                U[i, j, t] = U[i, j - 1, t - 1]
                V[i, j, t] = V[i, j - 1, t - 1]

    data = {'U': U, 'V': V, 'P': P}
    dimensions = {'time': time, 'lon': lon, 'lat': lat}
    if periodic_wrap:
        return FieldSet.from_data(data, dimensions, mesh='flat', transpose=True, time_periodic=delta(days=1))
    else:
        return FieldSet.from_data(data, dimensions, mesh='flat', transpose=True, allow_time_extrapolation=True)


def stommel_fieldset_from_xarray(xdim=200, ydim=200, periodic_wrap=False):
    """Simulate a periodic current along a western boundary, with significantly
    larger velocities along the western edge than the rest of the region

    The original test description can be found in: N. Fabbroni, 2009,
    Numerical Simulation of Passive tracers dispersion in the sea,
    Ph.D. dissertation, University of Bologna
    http://amsdottorato.unibo.it/1733/1/Fabbroni_Nicoletta_Tesi.pdf
    """
    # Coordinates of the test fieldset (on A-grid in deg)
    lon = np.linspace(0., a, xdim, dtype=np.float32)
    lat = np.linspace(0., b, ydim, dtype=np.float32)
    totime = ydim*24.0*60.0*60.0
    time = np.linspace(0., totime, ydim, dtype=np.float64)
    # Define arrays U (zonal), V (meridional), W (vertical) and P (sea
    # surface height) all on A-grid
    U = np.zeros((lon.size, lat.size, time.size), dtype=np.float32)
    V = np.zeros((lon.size, lat.size, time.size), dtype=np.float32)
    P = np.zeros((lon.size, lat.size, time.size), dtype=np.float32)

    beta = 2e-11
    r = 1/(11.6*86400)
    es = r/(beta*a)

    for i in range(lat.size):
        for j in range(lon.size):
            xi = lon[j] / a
            yi = lat[i] / b
            P[i, j, 0] = (1 - math.exp(-xi/es) - xi) * math.pi * np.sin(math.pi*yi)*scalefac
            U[i, j, 0] = -(1 - math.exp(-xi/es) - xi) * math.pi**2 * np.cos(math.pi*yi)*scalefac
            V[i, j, 0] = (math.exp(-xi/es)/es - 1) * math.pi * np.sin(math.pi*yi)*scalefac

    for t in range(1, time.size):
        for i in range(lon.size):
            for j in range(lat.size):
                P[i, j, t] = P[i, j - 1, t - 1]
                U[i, j, t] = U[i, j - 1, t - 1]
                V[i, j, t] = V[i, j - 1, t - 1]

    dimensions = {'time': time, 'lon': lon, 'lat': lat}
    dims = ('lon', 'lat', 'time')
    data = {'Uxr': xr.DataArray(U, coords=dimensions, dims=dims),
            'Vxr': xr.DataArray(V, coords=dimensions, dims=dims),
            'Pxr': xr.DataArray(P, coords=dimensions, dims=dims)}
    ds = xr.Dataset(data)

    pvariables = {'U': 'Uxr', 'V': 'Vxr', 'P': 'Pxr'}
    pdimensions = {'time': 'time', 'lat': 'lat', 'lon': 'lon'}
    if periodic_wrap:
        return FieldSet.from_xarray_dataset(ds, pvariables, pdimensions, mesh='flat', time_periodic=delta(days=1))
    else:
        return FieldSet.from_xarray_dataset(ds, pvariables, pdimensions, mesh='flat', allow_time_extrapolation=True)


class StommelParticleJ(JITParticle):
    p = Variable('p', dtype=np.float32, initial=.0)
    p0 = Variable('p0', dtype=np.float32, initial=.0)

class StommelParticleS(ScipyParticle):
    p = Variable('p', dtype=np.float32, initial=.0)
    p0 = Variable('p0', dtype=np.float32, initial=.0)

class AgeParticle_JIT(StommelParticleJ):
    age = Variable('age', dtype=np.float64, initial=0.0)
    life_expectancy = Variable('life_expectancy', dtype=np.float64, initial=np.finfo(np.float64).max)
    initialized_dynamic = Variable('initialized_dynamic', dtype=np.int32, initial=0)

class AgeParticle_SciPy(StommelParticleS):
    age = Variable('age', dtype=np.float64, initial=0.0)
    life_expectancy = Variable('life_expectancy', dtype=np.float64, initial=np.finfo(np.float64).max)
    initialized_dynamic = Variable('initialized_dynamic', dtype=np.int32, initial=0)

def initialize(particle, fieldset, time):
    if particle.initialized_dynamic < 1:
        particle.life_expectancy = time+random.uniform(.0, fieldset.life_expectancy)*math.sqrt(3.0/2.0)
        particle.initialized_dynamic = 1

def Age(particle, fieldset, time):
    if particle.state == ErrorCode.Evaluate:
        particle.age = particle.age + math.fabs(particle.dt)

    if particle.age > particle.life_expectancy:
        particle.delete()

ptype = {'scipy': StommelParticleS, 'jit': StommelParticleJ}
age_ptype = {'scipy': AgeParticle_SciPy, 'jit': AgeParticle_JIT}

if __name__=='__main__':
    parser = ArgumentParser(description="Example of particle advection using in-memory stommel test case")
    parser.add_argument("-i", "--imageFileName", dest="imageFileName", type=str, default="mpiChunking_plot_MPI.png", help="image file name of the plot")
    parser.add_argument("-b", "--backwards", dest="backwards", action='store_true', default=False, help="enable/disable running the simulation backwards")
    parser.add_argument("-p", "--periodic", dest="periodic", action='store_true', default=False, help="enable/disable periodic wrapping (else: extrapolation)")
    parser.add_argument("-r", "--release", dest="release", action='store_true', default=False, help="continuously add particles via repeatdt (default: False)")
    parser.add_argument("-rt", "--releasetime", dest="repeatdt", type=int, default=720, help="repeating release rate of added particles in Minutes (default: 720min = 12h)")
    parser.add_argument("-a", "--aging", dest="aging", action='store_true', default=False, help="Removed aging particles dynamically (default: False)")
    parser.add_argument("-t", "--time_in_days", dest="time_in_days", type=int, default=1, help="runtime in days (default: 1)")
    parser.add_argument("-x", "--xarray", dest="use_xarray", action='store_true', default=False, help="use xarray as data backend")
    parser.add_argument("-w", "--writeout", dest="write_out", action='store_true', default=False, help="write data in outfile")
    parser.add_argument("-d", "--delParticle", dest="delete_particle", action='store_true', default=False, help="switch to delete a particle (True) or reset a particle (default: False).")
    parser.add_argument("-A", "--animate", dest="animate", action='store_true', default=False, help="animate the particle trajectories during the run or not (default: False).")
    parser.add_argument("-V", "--visualize", dest="visualize", action='store_true', default=False, help="Visualize particle trajectories at the end (default: False). Requires -w in addition to take effect.")
    parser.add_argument("-N", "--n_particles", dest="nparticles", type=str, default="2**6", help="number of particles to generate and advect (default: 2e6)")
    parser.add_argument("-sN", "--start_n_particles", dest="start_nparticles", type=str, default="96", help="(optional) number of particles generated per release cycle (if --rt is set) (default: 96)")
    parser.add_argument("-m", "--mode", dest="compute_mode", choices=['jit','scipy'], default="jit", help="computation mode = [JIT, SciPp]")
    parser.add_argument("-G", "--GC", dest="useGC", action='store_true', default=False, help="using a garbage collector (default: false)")
    args = parser.parse_args()

    imageFileName=args.imageFileName
    periodicFlag=args.periodic
    backwardSimulation = args.backwards
    repeatdtFlag=args.release
    repeatRateMinutes=args.repeatdt
    time_in_days = args.time_in_days
    use_xarray = args.use_xarray
    agingParticles = args.aging
    with_GC = args.useGC
    Nparticle = int(float(eval(args.nparticles)))
    start_N_particles = int(float(eval(args.start_nparticles)))
    if MPI:
        mpi_comm = MPI.COMM_WORLD
        if mpi_comm.Get_rank() == 0:
            if agingParticles and not repeatdtFlag:
                sys.stdout.write("N: {} ( {} )\n".format(Nparticle, int(Nparticle * math.sqrt(3.0/2.0))))
            else:
                sys.stdout.write("N: {}\n".format(Nparticle))
    else:
        if agingParticles and not repeatdtFlag:
            sys.stdout.write("N: {} ( {} )\n".format(Nparticle, int(Nparticle * math.sqrt(3.0/2.0))))
        else:
            sys.stdout.write("N: {}\n".format(Nparticle))

    dt_minutes = 60
    #dt_minutes = 20
    #random.seed(123456)
    nowtime = datetime.datetime.now()
    random.seed(nowtime.microsecond)

    odir = ""
    if os.uname()[1] in ['science-bs35', 'science-bs36']:  # Gemini
        # odir = "/scratch/{}/experiments".format(os.environ['USER'])
        odir = "/scratch/{}/experiments".format("ckehl")
    elif fnmatch.fnmatchcase(os.uname()[1], "int?.*"):  # Cartesius
        CARTESIUS_SCRATCH_USERNAME = 'ckehl'
        odir = "/scratch/shared/{}/experiments/parcels".format(CARTESIUS_SCRATCH_USERNAME)
    else:
        odir = "/var/scratch/experiments"

    func_time = []
    mem_used_GB = []

    np.random.seed(0)
    fieldset = None
    if use_xarray:
        fieldset = stommel_fieldset_from_xarray(200, 200, periodic_wrap=periodicFlag)
    else:
        fieldset = stommel_fieldset_from_numpy(200, 200, periodic_wrap=periodicFlag)

    if args.compute_mode is 'scipy':
        Nparticle = 2**10

    if MPI:
        mpi_comm = MPI.COMM_WORLD
        mpi_rank = mpi_comm.Get_rank()
        if mpi_rank==0:
            # global_t_0 = ostime.time()
            # global_t_0 = MPI.Wtime()
            global_t_0 = ostime.process_time()
    else:
        # global_t_0 = ostime.time()
        global_t_0 = ostime.process_time()

    simStart = None
    for f in fieldset.get_fields():
        if type(f) in [VectorField, NestedField, SummedField]:  # or not f.grid.defer_load
            continue
        else:
            if backwardSimulation:
                simStart=f.grid.time_full[-1]
            else:
                simStart = f.grid.time_full[0]
            break

    if agingParticles:
        if not repeatdtFlag:
            Nparticle = int(Nparticle * math.sqrt(3.0/2.0))
        fieldset.add_constant('life_expectancy', delta(days=time_in_days).total_seconds())
    if repeatdtFlag:
        addParticleN = Nparticle/2.0
        refresh_cycle = (delta(days=time_in_days).total_seconds() / (addParticleN/start_N_particles)) / math.sqrt(3/2)
        repeatRateMinutes = int(refresh_cycle/60.0) if repeatRateMinutes == 720 else repeatRateMinutes

    if backwardSimulation:
        # ==== backward simulation ==== #
        if agingParticles:
            if repeatdtFlag:
                pset = ParticleSet(fieldset=fieldset, pclass=age_ptype[(args.compute_mode).lower()], lon=np.random.rand(start_N_particles, 1) * a, lat=np.random.rand(start_N_particles, 1) * b, time=simStart, repeatdt=delta(minutes=repeatRateMinutes))
                psetA = ParticleSet(fieldset=fieldset, pclass=age_ptype[(args.compute_mode).lower()], lon=np.random.rand(int(Nparticle/2.0), 1) * a, lat=np.random.rand(int(Nparticle/2.0), 1) * b, time=simStart)
                pset.add(psetA)
            else:
                pset = ParticleSet(fieldset=fieldset, pclass=age_ptype[(args.compute_mode).lower()], lon=np.random.rand(Nparticle, 1) * a, lat=np.random.rand(Nparticle, 1) * b, time=simStart)
        else:
            if repeatdtFlag:
                pset = ParticleSet(fieldset=fieldset, pclass=ptype[(args.compute_mode).lower()], lon=np.random.rand(start_N_particles, 1) * a, lat=np.random.rand(start_N_particles, 1) * b, time=simStart, repeatdt=delta(minutes=repeatRateMinutes))
                psetA = ParticleSet(fieldset=fieldset, pclass=ptype[(args.compute_mode).lower()], lon=np.random.rand(int(Nparticle/2.0), 1) * a, lat=np.random.rand(int(Nparticle/2.0), 1) * b, time=simStart)
                pset.add(psetA)
            else:
                pset = ParticleSet(fieldset=fieldset, pclass=ptype[(args.compute_mode).lower()], lon=np.random.rand(Nparticle, 1) * a, lat=np.random.rand(Nparticle, 1) * b, time=simStart)
    else:
        # ==== forward simulation ==== #
        if agingParticles:
            if repeatdtFlag:
                pset = ParticleSet(fieldset=fieldset, pclass=age_ptype[(args.compute_mode).lower()], lon=np.random.rand(start_N_particles, 1) * a, lat=np.random.rand(start_N_particles, 1) * b, time=simStart, repeatdt=delta(minutes=repeatRateMinutes))
                psetA = ParticleSet(fieldset=fieldset, pclass=age_ptype[(args.compute_mode).lower()], lon=np.random.rand(int(Nparticle/2.0), 1) * a, lat=np.random.rand(int(Nparticle/2.0), 1) * b, time=simStart)
                pset.add(psetA)
            else:
                pset = ParticleSet(fieldset=fieldset, pclass=age_ptype[(args.compute_mode).lower()], lon=np.random.rand(Nparticle, 1) * a, lat=np.random.rand(Nparticle, 1) * b, time=simStart)
        else:
            if repeatdtFlag:
                pset = ParticleSet(fieldset=fieldset, pclass=ptype[(args.compute_mode).lower()], lon=np.random.rand(start_N_particles, 1) * a, lat=np.random.rand(start_N_particles, 1) * b, time=simStart, repeatdt=delta(minutes=repeatRateMinutes))
                psetA = ParticleSet(fieldset=fieldset, pclass=ptype[(args.compute_mode).lower()], lon=np.random.rand(int(Nparticle/2.0), 1) * a, lat=np.random.rand(int(Nparticle/2.0), 1) * b, time=simStart)
                pset.add(psetA)
            else:
                pset = ParticleSet(fieldset=fieldset, pclass=ptype[(args.compute_mode).lower()], lon=np.random.rand(Nparticle, 1) * a, lat=np.random.rand(Nparticle, 1) * b, time=simStart)

    # if agingParticles:
    #     for p in pset.particles:
    #         p.life_expectancy = delta(days=time_in_days).total_seconds()
    # else:
    #     for p in pset.particles:
    #         p.initialized_dynamic = 1

    # available_n_particles = len(pset)
    # life = np.random.uniform(delta(hours=24).total_seconds(), delta(days=time_in_days).total_seconds(), available_n_particles)
    # i=0
    # for p in pset.particles:
    #     p.life_expectancy = life[i]
    #     i += 1

    output_file = None
    out_fname = "benchmark_stommel"
    if args.write_out:
        if MPI and (MPI.COMM_WORLD.Get_size()>1):
            out_fname += "_MPI"
        else:
            out_fname += "_noMPI"
        out_fname += "_n"+str(Nparticle)
        if backwardSimulation:
            out_fname += "_bwd"
        else:
            out_fname += "_fwd"
        if repeatdtFlag:
            out_fname += "_add"
        if agingParticles:
            out_fname += "_age"
        output_file = pset.ParticleFile(name=os.path.join(odir,out_fname+".nc"), outputdt=delta(hours=24))
    delete_func = RenewParticle
    if args.delete_particle:
        delete_func=DeleteParticle
    postProcessFuncs = []

    if MPI:
        mpi_comm = MPI.COMM_WORLD
        mpi_rank = mpi_comm.Get_rank()
        if mpi_rank==0:
            # starttime = ostime.time()
            # starttime = MPI.Wtime()
            starttime = ostime.process_time()
    else:
        # starttime = ostime.time()
        starttime = ostime.process_time()
    kernels = pset.Kernel(AdvectionRK4,delete_cfiles=True)
    if agingParticles:
        kernels += pset.Kernel(initialize, delete_cfiles=True)
        kernels += pset.Kernel(Age, delete_cfiles=True)
    if with_GC:
        postProcessFuncs.append(perIterGC)
    if backwardSimulation:
        # ==== backward simulation ==== #
        if args.animate:
            pset.execute(kernels, runtime=delta(days=time_in_days), dt=delta(minutes=-dt_minutes), output_file=output_file, recovery={ErrorCode.ErrorOutOfBounds: delete_func}, postIterationCallbacks=postProcessFuncs, callbackdt=delta(hours=12), moviedt=delta(hours=6), movie_background_field=fieldset.U)
        else:
            pset.execute(kernels, runtime=delta(days=time_in_days), dt=delta(minutes=-dt_minutes), output_file=output_file, recovery={ErrorCode.ErrorOutOfBounds: delete_func}, postIterationCallbacks=postProcessFuncs, callbackdt=delta(hours=12))
    else:
        # ==== forward simulation ==== #
        if args.animate:
            pset.execute(kernels, runtime=delta(days=time_in_days), dt=delta(minutes=dt_minutes), output_file=output_file, recovery={ErrorCode.ErrorOutOfBounds: delete_func}, postIterationCallbacks=postProcessFuncs, callbackdt=delta(hours=12), moviedt=delta(hours=6), movie_background_field=fieldset.U)
        else:
            pset.execute(kernels, runtime=delta(days=time_in_days), dt=delta(minutes=dt_minutes), output_file=output_file, recovery={ErrorCode.ErrorOutOfBounds: delete_func}, postIterationCallbacks=postProcessFuncs, callbackdt=delta(hours=12))
    if MPI:
        mpi_comm = MPI.COMM_WORLD
        mpi_rank = mpi_comm.Get_rank()
        if mpi_rank==0:
            # endtime = ostime.time()
            # endtime = MPI.Wtime()
            endtime = ostime.process_time()
    else:
        #endtime = ostime.time()
        endtime = ostime.process_time()

    # if MPI:
    #     mpi_comm = MPI.COMM_WORLD
    #     if mpi_comm.Get_rank() == 0:
    #         dt_time = []
    #         for i in range(len(perflog.times_steps)):
    #             if i==0:
    #                 dt_time.append( (perflog.times_steps[i]-global_t_0) )
    #             else:
    #                 dt_time.append( (perflog.times_steps[i]-perflog.times_steps[i-1]) )
    #         sys.stdout.write("final # particles: {}\n".format(perflog.Nparticles_step[len(perflog.Nparticles_step)-1]))
    #         sys.stdout.write("Time of pset.execute(): {} sec.\n".format(endtime-starttime))
    #         avg_time = np.mean(np.array(dt_time, dtype=np.float64))
    #         sys.stdout.write("Avg. kernel update time: {} msec.\n".format(avg_time*1000.0))
    # else:
    #     dt_time = []
    #     for i in range(len(perflog.times_steps)):
    #         if i == 0:
    #             dt_time.append((perflog.times_steps[i] - global_t_0))
    #         else:
    #             dt_time.append((perflog.times_steps[i] - perflog.times_steps[i - 1]))
    #     sys.stdout.write("final # particles: {}\n".format(perflog.Nparticles_step[len(perflog.Nparticles_step)-1]))
    #     sys.stdout.write("Time of pset.execute(): {} sec.\n".format(endtime - starttime))
    #     avg_time = np.mean(np.array(dt_time, dtype=np.float64))
    #     sys.stdout.write("Avg. kernel update time: {} msec.\n".format(avg_time * 1000.0))

    size_Npart = len(pset.nparticle_log)
    Npart = pset.nparticle_log.get_param(size_Npart-1)
    if MPI:
        mpi_comm = MPI.COMM_WORLD
        Npart = mpi_comm.reduce(Npart, op=MPI.SUM, root=0)
        if mpi_comm.Get_rank() == 0:
            if size_Npart>0:
                sys.stdout.write("final # particles: {}\n".format( Npart ))
            sys.stdout.write("Time of pset.execute(): {} sec.\n".format(endtime-starttime))
            avg_time = np.mean(np.array(pset.total_log.get_values(), dtype=np.float64))
            sys.stdout.write("Avg. kernel update time: {} msec.\n".format(avg_time*1000.0))
    else:
        if size_Npart > 0:
            sys.stdout.write("final # particles: {}\n".format( Npart ))
        sys.stdout.write("Time of pset.execute(): {} sec.\n".format(endtime - starttime))
        avg_time = np.mean(np.array(pset.total_log.get_values(), dtype=np.float64))
        sys.stdout.write("Avg. kernel update time: {} msec.\n".format(avg_time * 1000.0))

    # if args.write_out:
    #     output_file.close()
    #     if args.visualize:
    #         if MPI:
    #             mpi_comm = MPI.COMM_WORLD
    #             if mpi_comm.Get_rank() == 0:
    #                 plotTrajectoriesFile_loadedField(os.path.join(odir, out_fname+".nc"), tracerfield=fieldset.U)
    #         else:
    #             plotTrajectoriesFile_loadedField(os.path.join(odir, out_fname+".nc"),tracerfield=fieldset.U)

    if MPI:
        mpi_comm = MPI.COMM_WORLD
        Nparticles = mpi_comm.reduce(np.array(pset.nparticle_log.get_params()), op=MPI.SUM, root=0)
        Nmem = mpi_comm.reduce(np.array(pset.mem_log.get_params()), op=MPI.SUM, root=0)
        if mpi_comm.Get_rank() == 0:
            # plot(perflog.samples, perflog.times_steps, perflog.memory_steps, perflog.Nparticles_step, os.path.join(odir, imageFileName))
            # plot_internal(pset.total_log.get_values(), pset.compute_log.get_values(), pset.io_log.get_values(), pset.mem_log.get_params(), pset.nparticle_log.get_params(), imageFileName)
            plot_internal(pset.total_log.get_values(), pset.compute_log.get_values(), pset.io_log.get_values(), Nmem, Nparticles, imageFileName)
    else:
        # plot(perflog.samples, perflog.times_steps, perflog.memory_steps, perflog.Nparticles_step, os.path.join(odir, imageFileName))
        plot_internal(pset.total_log.get_values(), pset.compute_log.get_values(), pset.io_log.get_values(), pset.mem_log.get_params(), pset.nparticle_log.get_params(), imageFileName)


