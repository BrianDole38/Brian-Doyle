from parcels import (FieldSet, Field, NestedField, ScipyParticle, JITParticle, Geographic,
                     AdvectionRK4, AdvectionRK4_3D, Variable, ErrorCode)
from parcels import ParticleSetSOA, ParticleFileSOA, KernelSOA  # noqa
from parcels import ParticleSetAOS, ParticleFileAOS, KernelAOS  # noqa
import numpy as np
import pytest
from math import cos, pi
from datetime import timedelta as delta

pset_modes = ['soa', 'aos']
ptype = {'scipy': ScipyParticle, 'jit': JITParticle}
pset_type = {'soa': {'pset': ParticleSetSOA, 'pfile': ParticleFileSOA, 'kernel': KernelSOA},
             'aos': {'pset': ParticleSetAOS, 'pfile': ParticleFileAOS, 'kernel': KernelAOS}}


def pclass(mode):
    class SampleParticle(ptype[mode]):
        u = Variable('u', dtype=np.float32)
        v = Variable('v', dtype=np.float32)
        p = Variable('p', dtype=np.float32)
    return SampleParticle


def k_sample_uv():
    def SampleUV(particle, fieldset, time):
        (particle.u, particle.v) = fieldset.UV[time, particle.depth, particle.lat, particle.lon]
    return SampleUV


@pytest.fixture(name="k_sample_uv")
def k_sample_uv_fixture():
    return k_sample_uv()


def k_sample_uv_noconvert():
    def SampleUVNoConvert(particle, fieldset, time):
        (particle.u, particle.v) = fieldset.UV.eval(time, particle.depth, particle.lat, particle.lon, applyConversion=False)
    return SampleUVNoConvert


@pytest.fixture(name="k_sample_uv_noconvert")
def k_sample_uv_noconvert_fixture():
    return k_sample_uv_noconvert()


def k_sample_p():
    def SampleP(particle, fieldset, time):
        particle.p = fieldset.P[particle]
    return SampleP


@pytest.fixture(name="k_sample_p")
def k_sample_P_fixture():
    return k_sample_p()


def fieldset(xdim=200, ydim=100):
    """ Standard fieldset spanning the earth's coordinates with U and V
        equivalent to longitude and latitude in deg.
    """
    lon = np.linspace(-180, 180, xdim, dtype=np.float32)
    lat = np.linspace(-90, 90, ydim, dtype=np.float32)
    U, V = np.meshgrid(lat, lon)
    data = {'U': U, 'V': V}
    dimensions = {'lon': lon, 'lat': lat}

    return FieldSet.from_data(data, dimensions, mesh='flat', transpose=True)


@pytest.fixture(name="fieldset")
def fieldset_fixture(xdim=200, ydim=100):
    return fieldset(xdim=xdim, ydim=ydim)


def fieldset_geometric(xdim=200, ydim=100):
    """ Standard earth fieldset with U and V equivalent to lon/lat in m. """
    lon = np.linspace(-180, 180, xdim, dtype=np.float32)
    lat = np.linspace(-90, 90, ydim, dtype=np.float32)
    U, V = np.meshgrid(lat, lon)
    U *= 1000. * 1.852 * 60.
    V *= 1000. * 1.852 * 60.
    data = {'U': U, 'V': V}
    dimensions = {'lon': lon, 'lat': lat}
    fieldset = FieldSet.from_data(data, dimensions, transpose=True)
    fieldset.U.units = Geographic()
    fieldset.V.units = Geographic()
    return fieldset


@pytest.fixture(name="fieldset_geometric")
def fieldset_geometric_fixture(xdim=200, ydim=100):
    return fieldset_geometric(xdim=xdim, ydim=ydim)


def fieldset_geometric_polar(xdim=200, ydim=100):
    """ Standard earth fieldset with U and V equivalent to lon/lat in m
        and the inversion of the pole correction applied to U.
    """
    lon = np.linspace(-180, 180, xdim, dtype=np.float32)
    lat = np.linspace(-90, 90, ydim, dtype=np.float32)
    U, V = np.meshgrid(lat, lon)
    # Apply inverse of pole correction to U
    for i, y in enumerate(lat):
        U[:, i] *= cos(y * pi / 180)
    U *= 1000. * 1.852 * 60.
    V *= 1000. * 1.852 * 60.
    data = {'U': U, 'V': V}
    dimensions = {'lon': lon, 'lat': lat}
    return FieldSet.from_data(data, dimensions, mesh='spherical', transpose=True)


@pytest.fixture(name="fieldset_geometric_polar")
def fieldset_geometric_polar_fixture(xdim=200, ydim=100):
    return fieldset_geometric_polar(xdim=xdim, ydim=ydim)


def test_fieldset_sample(fieldset, xdim=120, ydim=80):
    """ Sample the fieldset using indexing notation. """
    lon = np.linspace(-170, 170, xdim, dtype=np.float32)
    lat = np.linspace(-80, 80, ydim, dtype=np.float32)
    v_s = np.array([fieldset.UV[0, 0., 70., x][1] for x in lon])
    u_s = np.array([fieldset.UV[0, 0., y, -45.][0] for y in lat])
    assert np.allclose(v_s, lon, rtol=1e-7)
    assert np.allclose(u_s, lat, rtol=1e-7)


def test_fieldset_sample_eval(fieldset, xdim=60, ydim=60):
    """ Sample the fieldset using the explicit eval function. """
    lon = np.linspace(-170, 170, xdim, dtype=np.float32)
    lat = np.linspace(-80, 80, ydim, dtype=np.float32)
    v_s = np.array([fieldset.UV.eval(0, 0., 70., x)[1] for x in lon])
    u_s = np.array([fieldset.UV.eval(0, 0., y, 0.)[0] for y in lat])
    assert np.allclose(v_s, lon, rtol=1e-7)
    assert np.allclose(u_s, lat, rtol=1e-7)


@pytest.mark.parametrize('pset_mode', pset_modes)
@pytest.mark.parametrize('mode', ['scipy', 'jit'])
def test_fieldset_polar_with_halo(fieldset_geometric_polar, pset_mode, mode):
    fieldset_geometric_polar.add_periodic_halo(zonal=5)
    pset = pset_type[pset_mode]['pset'](fieldset_geometric_polar, pclass=pclass(mode), lon=0, lat=0)
    pset.execute(runtime=1)
    assert pset.lon[0] == 0.


@pytest.mark.parametrize('pset_mode', pset_modes)
@pytest.mark.parametrize('mode', ['scipy', 'jit'])
def test_variable_init_from_field(pset_mode, mode, npart=9):
    dims = (2, 2)
    dimensions = {'lon': np.linspace(0., 1., dims[0], dtype=np.float32),
                  'lat': np.linspace(0., 1., dims[1], dtype=np.float32)}
    data = {'U': np.zeros(dims, dtype=np.float32),
            'V': np.zeros(dims, dtype=np.float32),
            'P': np.zeros(dims, dtype=np.float32)}
    data['P'][0, 0] = 1.
    fieldset = FieldSet.from_data(data, dimensions, mesh='flat', transpose=True)
    xv, yv = np.meshgrid(np.linspace(0, 1, int(np.sqrt(npart))), np.linspace(0, 1, int(np.sqrt(npart))))

    class VarParticle(pclass(mode)):
        a = Variable('a', dtype=np.float32, initial=fieldset.P)

    pset = pset_type[pset_mode]['pset'](fieldset, pclass=VarParticle, lon=xv.flatten(), lat=yv.flatten(), time=0)
    assert np.all([abs(pset.a[i] - fieldset.P[pset.time[i], pset.depth[i], pset.lat[i], pset.lon[i]]) < 1e-6 for i in range(pset.size)])


@pytest.mark.parametrize('pset_mode', pset_modes)
@pytest.mark.parametrize('mode', ['scipy', 'jit'])
def test_pset_from_field(pset_mode, mode, xdim=10, ydim=20, npart=10000):
    np.random.seed(123456)
    dimensions = {'lon': np.linspace(0., 1., xdim, dtype=np.float32),
                  'lat': np.linspace(0., 1., ydim, dtype=np.float32)}
    startfield = np.ones((xdim, ydim), dtype=np.float32)
    for x in range(xdim):
        startfield[x, :] = x
    data = {'U': np.zeros((xdim, ydim), dtype=np.float32),
            'V': np.zeros((xdim, ydim), dtype=np.float32),
            'start': startfield}
    fieldset = FieldSet.from_data(data, dimensions, mesh='flat', transpose=True)

    densfield = Field(name='densfield', data=np.zeros((xdim+1, ydim+1), dtype=np.float32),
                      lon=np.linspace(-1./(xdim*2), 1.+1./(xdim*2), xdim+1, dtype=np.float32),
                      lat=np.linspace(-1./(ydim*2), 1.+1./(ydim*2), ydim+1, dtype=np.float32), transpose=True)

    fieldset.add_field(densfield)
    pset = pset_type[pset_mode]['pset'].from_field(fieldset, size=npart, pclass=pclass(mode), start_field=fieldset.start)
    pdens = pset.density(field_name='densfield', relative=True)[:-1, :-1]
    assert np.allclose(np.transpose(pdens), startfield/np.sum(startfield), atol=1e-2)


@pytest.mark.parametrize('pset_mode', pset_modes)
@pytest.mark.parametrize('mode', ['scipy', 'jit'])
def test_nearest_neighbour_interpolation2D(pset_mode, mode, k_sample_p, npart=81):
    dims = (2, 2)
    dimensions = {'lon': np.linspace(0., 1., dims[0], dtype=np.float32),
                  'lat': np.linspace(0., 1., dims[1], dtype=np.float32)}
    data = {'U': np.zeros(dims, dtype=np.float32),
            'V': np.zeros(dims, dtype=np.float32),
            'P': np.zeros(dims, dtype=np.float32)}
    data['P'][0, 1] = 1.
    fieldset = FieldSet.from_data(data, dimensions, mesh='flat', transpose=True)
    fieldset.P.interp_method = 'nearest'
    xv, yv = np.meshgrid(np.linspace(0., 1.0, int(np.sqrt(npart))), np.linspace(0., 1.0, int(np.sqrt(npart))))
    pset = pset_type[pset_mode]['pset'](fieldset, pclass=pclass(mode), lon=xv.flatten(), lat=yv.flatten())
    pset.execute(k_sample_p, endtime=1, dt=1)
    assert np.allclose(pset.p[(pset.lon < 0.5) & (pset.lat > 0.5)], 1.0, rtol=1e-5)
    assert np.allclose(pset.p[(pset.lon > 0.5) | (pset.lat < 0.5)], 0.0, rtol=1e-5)


@pytest.mark.parametrize('pset_mode', pset_modes)
@pytest.mark.parametrize('mode', ['scipy', 'jit'])
def test_nearest_neighbour_interpolation3D(pset_mode, mode, k_sample_p, npart=81):
    dims = (2, 2, 2)
    dimensions = {'lon': np.linspace(0., 1., dims[0], dtype=np.float32),
                  'lat': np.linspace(0., 1., dims[1], dtype=np.float32),
                  'depth': np.linspace(0., 1., dims[2], dtype=np.float32)}
    data = {'U': np.zeros(dims, dtype=np.float32),
            'V': np.zeros(dims, dtype=np.float32),
            'P': np.zeros(dims, dtype=np.float32)}
    data['P'][0, 1, 1] = 1.
    fieldset = FieldSet.from_data(data, dimensions, mesh='flat', transpose=True)
    fieldset.P.interp_method = 'nearest'
    xv, yv = np.meshgrid(np.linspace(0, 1.0, int(np.sqrt(npart))), np.linspace(0, 1.0, int(np.sqrt(npart))))
    # combine a pset at 0m with pset at 1m, as meshgrid does not do 3D
    pset = pset_type[pset_mode]['pset'](fieldset, pclass=pclass(mode), lon=xv.flatten(), lat=yv.flatten(),
                                        depth=np.zeros(npart))
    pset2 = pset_type[pset_mode]['pset'](fieldset, pclass=pclass(mode), lon=xv.flatten(), lat=yv.flatten(),
                                         depth=np.ones(npart))
    pset.add(pset2)

    pset.execute(k_sample_p, endtime=1, dt=1)
    assert np.allclose(pset.p[(pset.lon < 0.5) & (pset.lat > 0.5) & (pset.depth > 0.5)], 1.0, rtol=1e-5)
    assert np.allclose(pset.p[(pset.lon > 0.5) | (pset.lat < 0.5) & (pset.depth < 0.5)], 0.0, rtol=1e-5)


@pytest.mark.parametrize('pset_mode', pset_modes)
@pytest.mark.parametrize('mode', ['scipy', 'jit'])
@pytest.mark.parametrize('arrtype', ['ones', 'rand'])
def test_inversedistance_nearland(pset_mode, mode, arrtype, k_sample_p, npart=81):
    dims = (4, 4, 6)
    P = np.random.rand(dims[0], dims[1], dims[2])+2 if arrtype == 'rand' else np.ones(dims, dtype=np.float32)
    P[1, 1:2, 1:6] = np.nan  # setting some values to land (NaN)
    dimensions = {'lon': np.linspace(0., 1., dims[2], dtype=np.float32),
                  'lat': np.linspace(0., 1., dims[1], dtype=np.float32),
                  'depth': np.linspace(0., 1., dims[0], dtype=np.float32)}
    data = {'U': np.zeros(dims, dtype=np.float32),
            'V': np.zeros(dims, dtype=np.float32),
            'P': P}
    fieldset = FieldSet.from_data(data, dimensions, mesh='flat')
    fieldset.P.interp_method = 'linear_invdist_land_tracer'

    xv, yv = np.meshgrid(np.linspace(0.1, 0.9, int(np.sqrt(npart))), np.linspace(0.1, 0.9, int(np.sqrt(npart))))
    # combine a pset at 0m with pset at 1m, as meshgrid does not do 3D
    pset = pset_type[pset_mode]['pset'](fieldset, pclass=pclass(mode), lon=xv.flatten(), lat=yv.flatten(),
                                        depth=np.zeros(npart))
    pset2 = pset_type[pset_mode]['pset'](fieldset, pclass=pclass(mode), lon=xv.flatten(), lat=yv.flatten(),
                                         depth=np.ones(npart))
    pset.add(pset2)
    pset.execute(k_sample_p, endtime=1, dt=1)
    if arrtype == 'rand':
        assert np.all((pset.p > 2) & (pset.p < 3))
    else:
        assert np.allclose(pset.p, 1.0, rtol=1e-5)

    success = False
    try:
        fieldset.U.interp_method = 'linear_invdist_land_tracer'
        fieldset.check_complete()
    except NotImplementedError:
        success = True
    assert success


@pytest.mark.parametrize('pset_mode', pset_modes)
@pytest.mark.parametrize('mode', ['scipy', 'jit'])
@pytest.mark.parametrize('boundaryslip', ['freeslip', 'partialslip'])
@pytest.mark.parametrize('withW', [False, True])
@pytest.mark.parametrize('withT', [False, True])
def test_partialslip_nearland_zonal(pset_mode, mode, boundaryslip, withW, withT, npart=20):
    dims = (3, 9, 3)
    U = 0.1*np.ones(dims, dtype=np.float32)
    U[:, 0, :] = np.nan
    U[:, -1, :] = np.nan
    V = np.zeros(dims, dtype=np.float32)
    V[:, 0, :] = np.nan
    V[:, -1, :] = np.nan
    dimensions = {'lon': np.linspace(-10, 10, dims[2]),
                  'lat': np.linspace(0., 4., dims[1], dtype=np.float32),
                  'depth': np.linspace(-10, 10, dims[0])}
    if withT:
        dimensions['time'] = [0, 1]
        U = np.tile(U, (2, 1, 1, 1))
        V = np.tile(V, (2, 1, 1, 1))
    if withW:
        W = 0.1 * np.ones(dims, dtype=np.float32)
        W[:, 0, :] = np.nan
        W[:, -1, :] = np.nan
        if withT:
            W = np.tile(W, (2, 1, 1, 1))
        data = {'U': U, 'V': V, 'W': W}
    else:
        data = {'U': U, 'V': V}
    fieldset = FieldSet.from_data(data, dimensions, mesh='flat', interp_method=boundaryslip)

    pset = pset_type[pset_mode]['pset'](fieldset, pclass=pclass(mode), lon=np.zeros(npart),
                                        lat=np.linspace(0.1, 3.9, npart), depth=np.zeros(npart))
    kernel = AdvectionRK4_3D if withW else AdvectionRK4
    pset.execute(kernel, endtime=1, dt=1)
    if boundaryslip == 'partialslip':
        assert np.allclose([p.lon for p in pset if p.lat >= 0.5 and p.lat <= 3.5], 0.1)
        assert np.allclose([pset[0].lon, pset[-1].lon], 0.06)
        assert np.allclose([pset[1].lon, pset[-2].lon], 0.08)
        if withW:
            assert np.allclose([p.depth for p in pset if p.lat >= 0.5 and p.lat <= 3.5], 0.1)
            assert np.allclose([pset[0].depth, pset[-1].depth], 0.06)
            assert np.allclose([pset[1].depth, pset[-2].depth], 0.08)
    else:
        assert np.allclose([p.lon for p in pset], 0.1)
        if withW:
            assert np.allclose([p.depth for p in pset], 0.1)


@pytest.mark.parametrize('pset_mode', pset_modes)
@pytest.mark.parametrize('mode', ['scipy', 'jit'])
@pytest.mark.parametrize('boundaryslip', ['freeslip', 'partialslip'])
@pytest.mark.parametrize('withW', [False, True])
def test_partialslip_nearland_meridional(pset_mode, mode, boundaryslip, withW, npart=20):
    dims = (1, 1, 9)
    U = np.zeros(dims, dtype=np.float32)
    U[:, :, 0] = np.nan
    U[:, :, -1] = np.nan
    V = 0.1*np.ones(dims, dtype=np.float32)
    V[:, :, 0] = np.nan
    V[:, :, -1] = np.nan
    dimensions = {'lon': np.linspace(0., 4., dims[2], dtype=np.float32), 'lat': 0, 'depth': 0}
    if withW:
        W = 0.1 * np.ones(dims, dtype=np.float32)
        W[:, :, 0] = np.nan
        W[:, :, -1] = np.nan
        data = {'U': U, 'V': V, 'W': W}
        interp_method = {'U': boundaryslip, 'V': boundaryslip, 'W': boundaryslip}
    else:
        data = {'U': U, 'V': V}
        interp_method = {'U': boundaryslip, 'V': boundaryslip}
    fieldset = FieldSet.from_data(data, dimensions, mesh='flat', interp_method=interp_method)

    pset = pset_type[pset_mode]['pset'](fieldset, pclass=pclass(mode), lat=np.zeros(npart),
                                        lon=np.linspace(0.1, 3.9, npart), depth=np.zeros(npart))
    kernel = AdvectionRK4_3D if withW else AdvectionRK4
    pset.execute(kernel, endtime=1, dt=1)
    if boundaryslip == 'partialslip':
        assert np.allclose([p.lat for p in pset if p.lon >= 0.5 and p.lon <= 3.5], 0.1)
        assert np.allclose([pset[0].lat, pset[-1].lat], 0.06)
        assert np.allclose([pset[1].lat, pset[-2].lat], 0.08)
        if withW:
            assert np.allclose([p.depth for p in pset if p.lon >= 0.5 and p.lon <= 3.5], 0.1)
            assert np.allclose([pset[0].depth, pset[-1].depth], 0.06)
            assert np.allclose([pset[1].depth, pset[-2].depth], 0.08)
    else:
        assert np.allclose([p.lat for p in pset], 0.1)
        if withW:
            assert np.allclose([p.depth for p in pset], 0.1)


@pytest.mark.parametrize('pset_mode', pset_modes)
@pytest.mark.parametrize('mode', ['scipy', 'jit'])
@pytest.mark.parametrize('boundaryslip', ['freeslip', 'partialslip'])
def test_partialslip_nearland_vertical(pset_mode, mode, boundaryslip, npart=20):
    dims = (9, 1, 1)
    U = 0.1*np.ones(dims, dtype=np.float32)
    U[0, :, :] = np.nan
    U[-1, :, :] = np.nan
    V = 0.1*np.ones(dims, dtype=np.float32)
    V[0, :, :] = np.nan
    V[-1, :, :] = np.nan
    dimensions = {'lon': 0, 'lat': 0, 'depth': np.linspace(0., 4., dims[0], dtype=np.float32)}
    data = {'U': U, 'V': V}
    fieldset = FieldSet.from_data(data, dimensions, mesh='flat', interp_method={'U': boundaryslip, 'V': boundaryslip})

    pset = pset_type[pset_mode]['pset'](fieldset, pclass=pclass(mode), lon=np.zeros(npart), lat=np.zeros(npart),
                                        depth=np.linspace(0.1, 3.9, npart))
    pset.execute(AdvectionRK4, endtime=1, dt=1)
    if boundaryslip == 'partialslip':
        assert np.allclose([p.lon for p in pset if p.depth >= 0.5 and p.depth <= 3.5], 0.1)
        assert np.allclose([p.lat for p in pset if p.depth >= 0.5 and p.depth <= 3.5], 0.1)
        assert np.allclose([pset[0].lon, pset[-1].lon, pset[0].lat, pset[-1].lat], 0.06)
        assert np.allclose([pset[1].lon, pset[-2].lon, pset[1].lat, pset[-2].lat], 0.08)
    else:
        assert np.allclose([p.lon for p in pset], 0.1)
        assert np.allclose([p.lat for p in pset], 0.1)


@pytest.mark.parametrize('pset_mode', pset_modes)
@pytest.mark.parametrize('mode', ['scipy', 'jit'])
@pytest.mark.parametrize('lat_flip', [False, True])
def test_fieldset_sample_particle(pset_mode, mode, k_sample_uv, lat_flip, npart=120):
    """ Sample the fieldset using an array of particles.

    Note that the low tolerances (1.e-6) are due to the first-order
    interpolation in JIT mode and give an indication of the
    corresponding sampling error.
    """

    lon = np.linspace(-180, 180, 200, dtype=np.float32)
    if lat_flip:
        lat = np.linspace(90, -90, 100, dtype=np.float32)
    else:
        lat = np.linspace(-90, 90, 100, dtype=np.float32)
    U, V = np.meshgrid(lat, lon)
    data = {'U': U, 'V': V}
    dimensions = {'lon': lon, 'lat': lat}

    fieldset = FieldSet.from_data(data, dimensions, mesh='flat', transpose=True)

    lon = np.linspace(-170, 170, npart)
    lat = np.linspace(-80, 80, npart)
    pset = pset_type[pset_mode]['pset'](fieldset, pclass=pclass(mode), lon=lon, lat=np.zeros(npart) + 70.)
    pset.execute(pset.Kernel(k_sample_uv), endtime=1., dt=1.)
    assert np.allclose(pset.v, lon, rtol=1e-6)

    pset = pset_type[pset_mode]['pset'](fieldset, pclass=pclass(mode), lat=lat, lon=np.zeros(npart) - 45.)
    pset.execute(pset.Kernel(k_sample_uv), endtime=1., dt=1.)
    assert np.allclose(pset.u, lat, rtol=1e-6)


@pytest.mark.parametrize('pset_mode', pset_modes)
@pytest.mark.parametrize('mode', ['scipy', 'jit'])
def test_fieldset_sample_geographic(fieldset_geometric, pset_mode, mode, k_sample_uv, npart=120):
    """ Sample a fieldset with conversion to geographic units (degrees). """
    fieldset = fieldset_geometric
    lon = np.linspace(-170, 170, npart)
    lat = np.linspace(-80, 80, npart)

    pset = pset_type[pset_mode]['pset'](fieldset, pclass=pclass(mode), lon=lon, lat=np.zeros(npart) + 70.)
    pset.execute(pset.Kernel(k_sample_uv), endtime=1., dt=1.)
    assert np.allclose(pset.v, lon, rtol=1e-6)

    pset = pset_type[pset_mode]['pset'](fieldset, pclass=pclass(mode), lat=lat, lon=np.zeros(npart) - 45.)
    pset.execute(pset.Kernel(k_sample_uv), endtime=1., dt=1.)
    assert np.allclose(pset.u, lat, rtol=1e-6)


@pytest.mark.parametrize('pset_mode', pset_modes)
@pytest.mark.parametrize('mode', ['scipy', 'jit'])
def test_fieldset_sample_geographic_noconvert(fieldset_geometric, pset_mode, mode, k_sample_uv_noconvert, npart=120):
    """ Sample a fieldset without conversion to geographic units. """
    fieldset = fieldset_geometric
    lon = np.linspace(-170, 170, npart)
    lat = np.linspace(-80, 80, npart)

    pset = pset_type[pset_mode]['pset'](fieldset, pclass=pclass(mode), lon=lon, lat=np.zeros(npart) + 70.)
    pset.execute(pset.Kernel(k_sample_uv_noconvert), endtime=1., dt=1.)
    assert np.allclose(pset.v, lon * 1000 * 1.852 * 60, rtol=1e-6)

    pset = pset_type[pset_mode]['pset'](fieldset, pclass=pclass(mode), lat=lat, lon=np.zeros(npart) - 45.)
    pset.execute(pset.Kernel(k_sample_uv_noconvert), endtime=1., dt=1.)
    assert np.allclose(pset.u, lat * 1000 * 1.852 * 60, rtol=1e-6)


@pytest.mark.parametrize('pset_mode', pset_modes)
@pytest.mark.parametrize('mode', ['scipy', 'jit'])
def test_fieldset_sample_geographic_polar(fieldset_geometric_polar, pset_mode, mode, k_sample_uv, npart=120):
    """ Sample a fieldset with conversion to geographic units and a pole correction. """
    fieldset = fieldset_geometric_polar
    lon = np.linspace(-170, 170, npart)
    lat = np.linspace(-80, 80, npart)

    pset = pset_type[pset_mode]['pset'](fieldset, pclass=pclass(mode), lon=lon, lat=np.zeros(npart) + 70.)
    pset.execute(pset.Kernel(k_sample_uv), endtime=1., dt=1.)
    assert np.allclose(pset.v, lon, rtol=1e-6)

    pset = pset_type[pset_mode]['pset'](fieldset, pclass=pclass(mode), lat=lat, lon=np.zeros(npart) - 45.)
    pset.execute(pset.Kernel(k_sample_uv), endtime=1., dt=1.)
    # Note: 1.e-2 is a very low rtol, so there seems to be a rather
    # large sampling error for the JIT correction.
    assert np.allclose(pset.u, lat, rtol=1e-2)


@pytest.mark.parametrize('pset_mode', pset_modes)
@pytest.mark.parametrize('mode', ['scipy', 'jit'])
def test_meridionalflow_spherical(pset_mode, mode, xdim=100, ydim=200):
    """ Create uniform NORTHWARD flow on spherical earth and advect particles

    As flow is so simple, it can be directly compared to analytical solution
    """

    maxvel = 1.
    dimensions = {'lon': np.linspace(-180, 180, xdim, dtype=np.float32),
                  'lat': np.linspace(-90, 90, ydim, dtype=np.float32)}
    data = {'U': np.zeros([xdim, ydim]),
            'V': maxvel * np.ones([xdim, ydim])}

    fieldset = FieldSet.from_data(data, dimensions, mesh='spherical', transpose=True)

    lonstart = [0, 45]
    latstart = [0, 45]
    runtime = delta(hours=24)
    pset = pset_type[pset_mode]['pset'](fieldset, pclass=pclass(mode), lon=lonstart, lat=latstart)
    pset.execute(pset.Kernel(AdvectionRK4), runtime=runtime, dt=delta(hours=1))

    assert pset.lat[0] - (latstart[0] + runtime.total_seconds() * maxvel / 1852 / 60) < 1e-4
    assert pset.lon[0] - lonstart[0] < 1e-4
    assert pset.lat[1] - (latstart[1] + runtime.total_seconds() * maxvel / 1852 / 60) < 1e-4
    assert pset.lon[1] - lonstart[1] < 1e-4


@pytest.mark.parametrize('pset_mode', pset_modes)
@pytest.mark.parametrize('mode', ['scipy', 'jit'])
def test_zonalflow_spherical(pset_mode, mode, k_sample_p, xdim=100, ydim=200):
    """ Create uniform EASTWARD flow on spherical earth and advect particles

    As flow is so simple, it can be directly compared to analytical solution
    Note that in this case the cosine conversion is needed
    """
    maxvel = 1.
    p_fld = 10
    dimensions = {'lon': np.linspace(-180, 180, xdim, dtype=np.float32),
                  'lat': np.linspace(-90, 90, ydim, dtype=np.float32)}
    data = {'U': maxvel * np.ones([xdim, ydim]),
            'V': np.zeros([xdim, ydim]),
            'P': p_fld * np.ones([xdim, ydim])}

    fieldset = FieldSet.from_data(data, dimensions, mesh='spherical', transpose=True)

    lonstart = [0, 45]
    latstart = [0, 45]
    runtime = delta(hours=24)
    pset = pset_type[pset_mode]['pset'](fieldset, pclass=pclass(mode), lon=lonstart, lat=latstart)
    pset.execute(pset.Kernel(AdvectionRK4) + k_sample_p,
                 runtime=runtime, dt=delta(hours=1))

    assert pset.lat[0] - latstart[0] < 1e-4
    assert pset.lon[0] - (lonstart[0] + runtime.total_seconds() * maxvel / 1852 / 60
                          / cos(latstart[0] * pi / 180)) < 1e-4
    assert abs(pset.p[0] - p_fld) < 1e-4
    assert pset.lat[1] - latstart[1] < 1e-4
    assert pset.lon[1] - (lonstart[1] + runtime.total_seconds() * maxvel / 1852 / 60
                          / cos(latstart[1] * pi / 180)) < 1e-4
    assert abs(pset.p[1] - p_fld) < 1e-4


@pytest.mark.parametrize('pset_mode', pset_modes)
@pytest.mark.parametrize('mode', ['scipy', 'jit'])
def test_random_field(pset_mode, mode, k_sample_p, xdim=20, ydim=20, npart=100):
    """Sampling test that tests for overshoots by sampling a field of
    random numbers between 0 and 1.
    """
    np.random.seed(123456)
    dimensions = {'lon': np.linspace(0., 1., xdim, dtype=np.float32),
                  'lat': np.linspace(0., 1., ydim, dtype=np.float32)}
    data = {'U': np.zeros((xdim, ydim), dtype=np.float32),
            'V': np.zeros((xdim, ydim), dtype=np.float32),
            'P': np.random.uniform(0, 1., size=(xdim, ydim)),
            'start': np.ones((xdim, ydim), dtype=np.float32)}

    fieldset = FieldSet.from_data(data, dimensions, mesh='flat', transpose=True)
    pset = pset_type[pset_mode]['pset'].from_field(fieldset, size=npart, pclass=pclass(mode),
                                                   start_field=fieldset.start)
    pset.execute(k_sample_p, endtime=1., dt=1.0)
    sampled = pset.p
    assert (sampled >= 0.).all()


@pytest.mark.parametrize('pset_mode', pset_modes)
@pytest.mark.parametrize('mode', ['scipy', 'jit'])
@pytest.mark.parametrize('allow_time_extrapolation', [True, False])
def test_sampling_out_of_bounds_time(pset_mode, mode, allow_time_extrapolation, k_sample_p,
                                     xdim=10, ydim=10, tdim=10):
    dimensions = {'lon': np.linspace(0., 1., xdim, dtype=np.float32),
                  'lat': np.linspace(0., 1., ydim, dtype=np.float32),
                  'time': np.linspace(0., 1., tdim, dtype=np.float64)}
    data = {'U': np.zeros((xdim, ydim, tdim), dtype=np.float32),
            'V': np.zeros((xdim, ydim, tdim), dtype=np.float32),
            'P': np.ones((xdim, ydim, 1), dtype=np.float32) * dimensions['time']}

    fieldset = FieldSet.from_data(data, dimensions, mesh='flat',
                                  allow_time_extrapolation=allow_time_extrapolation, transpose=True)
    pset = pset_type[pset_mode]['pset'](fieldset, pclass=pclass(mode), lon=[0.5], lat=[0.5], time=-1.0)
    if allow_time_extrapolation:
        pset.execute(k_sample_p, endtime=-0.9, dt=0.1)
        assert np.allclose(pset.p, 0.0, rtol=1e-5)
    else:
        with pytest.raises(RuntimeError):
            pset.execute(k_sample_p, endtime=-0.9, dt=0.1)

    pset = pset_type[pset_mode]['pset'](fieldset, pclass=pclass(mode), lon=[0.5], lat=[0.5], time=0)
    pset.execute(k_sample_p, runtime=0.1, dt=0.1)
    assert np.allclose(pset.p, 0.0, rtol=1e-5)

    pset = pset_type[pset_mode]['pset'](fieldset, pclass=pclass(mode), lon=[0.5], lat=[0.5], time=0.5)
    pset.execute(k_sample_p, runtime=0.1, dt=0.1)
    assert np.allclose(pset.p, 0.5, rtol=1e-5)

    pset = pset_type[pset_mode]['pset'](fieldset, pclass=pclass(mode), lon=[0.5], lat=[0.5], time=1.0)
    pset.execute(k_sample_p, runtime=0.1, dt=0.1)
    assert np.allclose(pset.p, 1.0, rtol=1e-5)

    pset = pset_type[pset_mode]['pset'](fieldset, pclass=pclass(mode), lon=[0.5], lat=[0.5], time=2.0)
    if allow_time_extrapolation:
        pset.execute(k_sample_p, runtime=0.1, dt=0.1)
        assert np.allclose(pset.p, 1.0, rtol=1e-5)
    else:
        with pytest.raises(RuntimeError):
            pset.execute(k_sample_p, runtime=0.1, dt=0.1)


@pytest.mark.parametrize('pset_mode', pset_modes)
@pytest.mark.parametrize('mode', ['jit', 'scipy'])
@pytest.mark.parametrize('npart', [1, 10])
@pytest.mark.parametrize('chs', [False, 'auto', {'lat': ('y', 10), 'lon': ('x', 10)}])
def test_sampling_multigrids_non_vectorfield_from_file(pset_mode, mode, npart, tmpdir, chs, filename='test_subsets'):
    xdim, ydim = 100, 200
    filepath = tmpdir.join(filename)
    U = Field('U', np.zeros((ydim, xdim), dtype=np.float32),
              lon=np.linspace(0., 1., xdim, dtype=np.float32),
              lat=np.linspace(0., 1., ydim, dtype=np.float32))
    V = Field('V', np.zeros((ydim, xdim), dtype=np.float32),
              lon=np.linspace(0., 1., xdim, dtype=np.float32),
              lat=np.linspace(0., 1., ydim, dtype=np.float32))
    B = Field('B', np.ones((3*ydim, 4*xdim), dtype=np.float32),
              lon=np.linspace(0., 1., 4*xdim, dtype=np.float32),
              lat=np.linspace(0., 1., 3*ydim, dtype=np.float32))
    fieldset = FieldSet(U, V)
    fieldset.add_field(B, 'B')
    fieldset.write(filepath)
    fieldset = None

    ufiles = [filepath+'U.nc', ] * 4
    vfiles = [filepath+'V.nc', ] * 4
    bfiles = [filepath+'B.nc', ] * 4
    timestamps = np.arange(0, 4, 1) * 86400.0
    timestamps = np.expand_dims(timestamps, 1)
    files = {'U': ufiles, 'V': vfiles, 'B': bfiles}
    variables = {'U': 'vozocrtx', 'V': 'vomecrty', 'B': 'B'}
    dimensions = {'lon': 'nav_lon', 'lat': 'nav_lat'}
    fieldset = FieldSet.from_netcdf(files, variables, dimensions, timestamps=timestamps, allow_time_extrapolation=True,
                                    chunksize=chs)

    fieldset.add_constant('sample_depth', 2.5)
    if chs == 'auto':
        assert fieldset.U.grid != fieldset.V.grid
    else:
        assert fieldset.U.grid is fieldset.V.grid
    assert fieldset.U.grid is not fieldset.B.grid

    class TestParticle(ptype[mode]):
        sample_var = Variable('sample_var', initial=0.)

    pset = pset_type[pset_mode]['pset'].from_line(fieldset, pclass=TestParticle, start=[0.3, 0.3], finish=[0.7, 0.7],
                                                  size=npart)

    def test_sample(particle, fieldset, time):
        particle.sample_var += fieldset.B[time, fieldset.sample_depth, particle.lat, particle.lon]

    kernels = pset.Kernel(AdvectionRK4) + pset.Kernel(test_sample)
    pset.execute(kernels, runtime=10, dt=1)
    assert np.allclose(pset.sample_var, 10.0)
    if mode == 'jit':
        if pset_mode == 'soa':
            assert len(pset.xi.shape) == 2
            assert pset.xi.shape[0] == len(pset.lon)
            assert pset.xi.shape[1] == fieldset.gridset.size
            assert np.all(pset.xi >= 0)
            assert np.all(pset.xi[:, fieldset.B.igrid] < xdim * 4)
            assert np.all(pset.xi[:, 0] < xdim)
            assert pset.yi.shape[0] == len(pset.lon)
            assert pset.yi.shape[1] == fieldset.gridset.size
            assert np.all(pset.yi >= 0)
            assert np.all(pset.yi[:, fieldset.B.igrid] < ydim * 3)
            assert np.all(pset.yi[:, 0] < ydim)
        elif pset_mode == 'aos':
            assert np.alltrue([[pxi > 0 for pxi in p.xi] for p in pset])
            assert np.alltrue([len(p.xi) == fieldset.gridset.size for p in pset])
            assert np.alltrue([p.xi[fieldset.B.igrid] < xdim * 4 for p in pset])
            assert np.alltrue([p.xi[0] < xdim for p in pset])
            assert np.alltrue([[pyi > 0 for pyi in p.yi] for p in pset])
            assert np.alltrue([len(p.yi) == fieldset.gridset.size for p in pset])
            assert np.alltrue([p.yi[fieldset.B.igrid] < ydim * 3 for p in pset])
            assert np.alltrue([p.yi[0] < ydim for p in pset])


@pytest.mark.parametrize('pset_mode', pset_modes)
@pytest.mark.parametrize('mode', ['jit', 'scipy'])
@pytest.mark.parametrize('npart', [1, 10])
def test_sampling_multigrids_non_vectorfield(pset_mode, mode, npart):
    xdim, ydim = 100, 200
    U = Field('U', np.zeros((ydim, xdim), dtype=np.float32),
              lon=np.linspace(0., 1., xdim, dtype=np.float32),
              lat=np.linspace(0., 1., ydim, dtype=np.float32))
    V = Field('V', np.zeros((ydim, xdim), dtype=np.float32),
              lon=np.linspace(0., 1., xdim, dtype=np.float32),
              lat=np.linspace(0., 1., ydim, dtype=np.float32))
    B = Field('B', np.ones((3*ydim, 4*xdim), dtype=np.float32),
              lon=np.linspace(0., 1., 4*xdim, dtype=np.float32),
              lat=np.linspace(0., 1., 3*ydim, dtype=np.float32))
    fieldset = FieldSet(U, V)
    fieldset.add_field(B, 'B')
    fieldset.add_constant('sample_depth', 2.5)
    assert fieldset.U.grid is fieldset.V.grid
    assert fieldset.U.grid is not fieldset.B.grid

    class TestParticle(ptype[mode]):
        sample_var = Variable('sample_var', initial=0.)

    pset = pset_type[pset_mode]['pset'].from_line(fieldset, pclass=TestParticle, start=[0.3, 0.3], finish=[0.7, 0.7],
                                                  size=npart)

    def test_sample(particle, fieldset, time):
        particle.sample_var += fieldset.B[time, fieldset.sample_depth, particle.lat, particle.lon]

    kernels = pset.Kernel(AdvectionRK4) + pset.Kernel(test_sample)
    pset.execute(kernels, runtime=10, dt=1)
    assert np.allclose(pset.sample_var, 10.0)
    if mode == 'jit':
        if pset_mode == 'soa':
            assert len(pset.xi.shape) == 2
            assert pset.xi.shape[0] == len(pset.lon)
            assert pset.xi.shape[1] == fieldset.gridset.size
            assert np.all(pset.xi >= 0)
            assert np.all(pset.xi[:, fieldset.B.igrid] < xdim * 4)
            assert np.all(pset.xi[:, 0] < xdim)
            assert pset.yi.shape[0] == len(pset.lon)
            assert pset.yi.shape[1] == fieldset.gridset.size
            assert np.all(pset.yi >= 0)
            assert np.all(pset.yi[:, fieldset.B.igrid] < ydim * 3)
            assert np.all(pset.yi[:, 0] < ydim)
        elif pset_mode == 'aos':
            assert np.alltrue([[pxi > 0 for pxi in p.xi] for p in pset])
            assert np.alltrue([len(p.xi) == fieldset.gridset.size for p in pset])
            assert np.alltrue([p.xi[fieldset.B.igrid] < xdim * 4 for p in pset])
            assert np.alltrue([p.xi[0] < xdim for p in pset])
            assert np.alltrue([[pyi > 0 for pyi in p.yi] for p in pset])
            assert np.alltrue([len(p.yi) == fieldset.gridset.size for p in pset])
            assert np.alltrue([p.yi[fieldset.B.igrid] < ydim * 3 for p in pset])
            assert np.alltrue([p.yi[0] < ydim for p in pset])


@pytest.mark.parametrize('pset_mode', pset_modes)
@pytest.mark.parametrize('mode', ['jit', 'scipy'])
@pytest.mark.parametrize('ugridfactor', [1, 10])
def test_sampling_multiple_grid_sizes(pset_mode, mode, ugridfactor):
    xdim, ydim = 10, 20
    U = Field('U', np.zeros((ydim*ugridfactor, xdim*ugridfactor), dtype=np.float32),
              lon=np.linspace(0., 1., xdim*ugridfactor, dtype=np.float32),
              lat=np.linspace(0., 1., ydim*ugridfactor, dtype=np.float32))
    V = Field('V', np.zeros((ydim, xdim), dtype=np.float32),
              lon=np.linspace(0., 1., xdim, dtype=np.float32),
              lat=np.linspace(0., 1., ydim, dtype=np.float32))
    fieldset = FieldSet(U, V)
    pset = pset_type[pset_mode]['pset'](fieldset, pclass=pclass(mode), lon=[0.8], lat=[0.9])

    if ugridfactor > 1:
        assert fieldset.U.grid is not fieldset.V.grid
    else:
        assert fieldset.U.grid is fieldset.V.grid
    pset.execute(AdvectionRK4, runtime=10, dt=1)
    assert np.isclose(pset.lon[0], 0.8)
    assert np.all((0 <= pset.xi) & (pset.xi < xdim*ugridfactor))


def test_multiple_grid_addlater_error():
    xdim, ydim = 10, 20
    U = Field('U', np.zeros((ydim, xdim), dtype=np.float32),
              lon=np.linspace(0., 1., xdim, dtype=np.float32),
              lat=np.linspace(0., 1., ydim, dtype=np.float32))
    V = Field('V', np.zeros((ydim, xdim), dtype=np.float32),
              lon=np.linspace(0., 1., xdim, dtype=np.float32),
              lat=np.linspace(0., 1., ydim, dtype=np.float32))
    fieldset = FieldSet(U, V)

    pset = pset_type['soa']['pset'](fieldset, pclass=pclass('jit'), lon=[0.8], lat=[0.9])  # noqa ; to trigger fieldset.check_complete

    P = Field('P', np.zeros((ydim*10, xdim*10), dtype=np.float32),
              lon=np.linspace(0., 1., xdim*10, dtype=np.float32),
              lat=np.linspace(0., 1., ydim*10, dtype=np.float32))

    fail = False
    try:
        fieldset.add_field(P)
    except RuntimeError:
        fail = True
    assert fail


@pytest.mark.parametrize('pset_mode', pset_modes)
@pytest.mark.parametrize('mode', ['jit', 'scipy'])
@pytest.mark.parametrize('with_W', [True, False])
@pytest.mark.parametrize('mesh', ['flat', 'spherical'])
def test_summedfields(pset_mode, mode, with_W, k_sample_p, mesh):
    xdim = 10
    ydim = 20
    zdim = 4
    gf = 10  # factor by which the resolution of grid1 is higher than of grid2
    U1 = Field('U', 0.2*np.ones((zdim*gf, ydim*gf, xdim*gf), dtype=np.float32),
               lon=np.linspace(0., 1., xdim*gf, dtype=np.float32),
               lat=np.linspace(0., 1., ydim*gf, dtype=np.float32),
               depth=np.linspace(0., 20., zdim*gf, dtype=np.float32),
               mesh=mesh)
    U2 = Field('U', 0.1*np.ones((zdim, ydim, xdim), dtype=np.float32),
               lon=np.linspace(0., 1., xdim, dtype=np.float32),
               lat=np.linspace(0., 1., ydim, dtype=np.float32),
               depth=np.linspace(0., 20., zdim, dtype=np.float32),
               mesh=mesh)
    V1 = Field('V', np.zeros((zdim*gf, ydim*gf, xdim*gf), dtype=np.float32), grid=U1.grid, fieldtype='V')
    V2 = Field('V', np.zeros((zdim, ydim, xdim), dtype=np.float32), grid=U2.grid, fieldtype='V')
    fieldsetS = FieldSet(U1+U2, V1+V2)

    conv = 1852*60 if mesh == 'spherical' else 1.
    assert np.allclose(fieldsetS.U[0, 0, 0, 0]*conv, 0.3)

    P1 = Field('P', 30*np.ones((zdim*gf, ydim*gf, xdim*gf), dtype=np.float32), grid=U1.grid)
    P2 = Field('P', 20*np.ones((zdim, ydim, xdim), dtype=np.float32), grid=U2.grid)
    P3 = Field('P', 10*np.ones((zdim, ydim, xdim), dtype=np.float32), grid=U2.grid)
    P4 = Field('P', 0*np.ones((zdim, ydim, xdim), dtype=np.float32), grid=U2.grid)
    fieldsetS.add_field((P1+P4)+(P2+P3), name='P')
    assert np.allclose(fieldsetS.P[0, 0, 0, 0], 60)

    def sample_UV_noconvert(particle, fieldset, time):
        (particle.u, particle.v) = fieldset.UV.eval(time, particle.depth, particle.lat, particle.lon, applyConversion=False)  # noqa

    if with_W:
        W1 = Field('W', 2*np.ones((zdim * gf, ydim * gf, xdim * gf), dtype=np.float32), grid=U1.grid)
        W2 = Field('W', np.ones((zdim, ydim, xdim), dtype=np.float32), grid=U2.grid)
        fieldsetS.add_field(W1+W2, name='W')
        pset = pset_type[pset_mode]['pset'](fieldsetS, pclass=pclass(mode), lon=[0], lat=[0.9])
        pset.execute(AdvectionRK4_3D+pset.Kernel(k_sample_p)+sample_UV_noconvert, runtime=2, dt=1)
        assert np.isclose(pset.depth[0], 6)
    else:
        pset = pset_type[pset_mode]['pset'](fieldsetS, pclass=pclass(mode), lon=[0], lat=[0.9])
        pset.execute(AdvectionRK4+pset.Kernel(k_sample_p)+sample_UV_noconvert, runtime=2, dt=1)
    assert np.isclose(pset.u[0], 0.3)
    assert np.isclose(pset.p[0], 60)
    assert np.isclose(pset.lon[0]*conv, 0.6, atol=1e-3)
    assert np.isclose(pset.lat[0], 0.9)
    assert np.allclose(fieldsetS.UV[0][0, 0, 0, 0], [.2/conv, 0])


@pytest.mark.parametrize('boundaryslip', ['freeslip', 'partialslip'])
def test_summedfields_slipinterp_warning(boundaryslip):
    xdim = 10
    ydim = 20
    zdim = 4
    gf = 10  # factor by which the resolution of grid1 is higher than of grid2
    U1 = Field('U', 0.2*np.ones((zdim*gf, ydim*gf, xdim*gf), dtype=np.float32),
               lon=np.linspace(0., 1., xdim*gf, dtype=np.float32),
               lat=np.linspace(0., 1., ydim*gf, dtype=np.float32),
               depth=np.linspace(0., 20., zdim*gf, dtype=np.float32),
               interp_method=boundaryslip)
    U2 = Field('U', 0.1*np.ones((zdim, ydim, xdim), dtype=np.float32),
               lon=np.linspace(0., 1., xdim, dtype=np.float32),
               lat=np.linspace(0., 1., ydim, dtype=np.float32),
               depth=np.linspace(0., 20., zdim, dtype=np.float32))
    V1 = Field('V', np.zeros((zdim*gf, ydim*gf, xdim*gf), dtype=np.float32), grid=U1.grid, fieldtype='V')
    V2 = Field('V', np.zeros((zdim, ydim, xdim), dtype=np.float32), grid=U2.grid, fieldtype='V')
    fieldsetS = FieldSet(U1+U2, V1+V2)

    with pytest.warns(UserWarning):
        fieldsetS.check_complete()


@pytest.mark.parametrize('pset_mode', pset_modes)
@pytest.mark.parametrize('mode', ['jit', 'scipy'])
def test_nestedfields(pset_mode, mode, k_sample_p):
    xdim = 10
    ydim = 20

    U1 = Field('U1', 0.1*np.ones((ydim, xdim), dtype=np.float32),
               lon=np.linspace(0., 1., xdim, dtype=np.float32),
               lat=np.linspace(0., 1., ydim, dtype=np.float32))
    V1 = Field('V1', 0.2*np.ones((ydim, xdim), dtype=np.float32),
               lon=np.linspace(0., 1., xdim, dtype=np.float32),
               lat=np.linspace(0., 1., ydim, dtype=np.float32))
    U2 = Field('U2', 0.3*np.ones((ydim, xdim), dtype=np.float32),
               lon=np.linspace(0., 2., xdim, dtype=np.float32),
               lat=np.linspace(0., 2., ydim, dtype=np.float32))
    V2 = Field('V2', 0.4*np.ones((ydim, xdim), dtype=np.float32),
               lon=np.linspace(0., 2., xdim, dtype=np.float32),
               lat=np.linspace(0., 2., ydim, dtype=np.float32))
    U = NestedField('U', [U1, U2])
    V = NestedField('V', [V1, V2])
    fieldset = FieldSet(U, V)

    P1 = Field('P1', 0.1*np.ones((ydim, xdim), dtype=np.float32),
               lon=np.linspace(0., 1., xdim, dtype=np.float32),
               lat=np.linspace(0., 1., ydim, dtype=np.float32))
    P2 = Field('P2', 0.2*np.ones((ydim, xdim), dtype=np.float32),
               lon=np.linspace(0., 2., xdim, dtype=np.float32),
               lat=np.linspace(0., 2., ydim, dtype=np.float32))
    P = NestedField('P', [P1, P2])
    fieldset.add_field(P)

    def Recover(particle, fieldset, time):
        particle.lon = -1
        particle.lat = -1
        particle.p = 999
        particle.time = particle.time + particle.dt

    pset = pset_type[pset_mode]['pset'](fieldset, pclass=pclass(mode), lon=[0], lat=[.3])
    pset.execute(AdvectionRK4+pset.Kernel(k_sample_p), runtime=1, dt=1)
    assert np.isclose(pset.lat[0], .5)
    assert np.isclose(pset.p[0], .1)
    pset = pset_type[pset_mode]['pset'](fieldset, pclass=pclass(mode), lon=[0], lat=[1.3])
    pset.execute(AdvectionRK4+pset.Kernel(k_sample_p), runtime=1, dt=1)
    assert np.isclose(pset.lat[0], 1.7)
    assert np.isclose(pset.p[0], .2)
    pset = pset_type[pset_mode]['pset'](fieldset, pclass=pclass(mode), lon=[0], lat=[2.3])
    pset.execute(AdvectionRK4+pset.Kernel(k_sample_p), runtime=1, dt=1, recovery={ErrorCode.ErrorOutOfBounds: Recover})
    assert np.isclose(pset.lat[0], -1)
    assert np.isclose(pset.p[0], 999)
    assert np.allclose(fieldset.UV[0][0, 0, 0, 0], [.1, .2])
