"""Collection of pre-built advection kernels"""
import math

from parcels.tools.statuscodes import OperationCode


__all__ = ['AdvectionRK4', 'AdvectionEE', 'AdvectionRK45', 'AdvectionRK4_3D',
           'AdvectionAnalytical']


def AdvectionRK4(particle, fieldset, time):
    """Advection of particles using fourth-order Runge-Kutta integration.

    Function needs to be converted to Kernel object before execution"""
    (u1, v1) = fieldset.UV[particle]
    lon1, lat1 = (particle.lon + u1*.5*particle.dt, particle.lat + v1*.5*particle.dt)
    (u2, v2) = fieldset.UV[time + .5 * particle.dt, particle.depth, lat1, lon1, particle]
    lon2, lat2 = (particle.lon + u2*.5*particle.dt, particle.lat + v2*.5*particle.dt)
    (u3, v3) = fieldset.UV[time + .5 * particle.dt, particle.depth, lat2, lon2, particle]
    lon3, lat3 = (particle.lon + u3*particle.dt, particle.lat + v3*particle.dt)
    (u4, v4) = fieldset.UV[time + particle.dt, particle.depth, lat3, lon3, particle]
    particle.lon += (u1 + 2*u2 + 2*u3 + u4) / 6. * particle.dt
    particle.lat += (v1 + 2*v2 + 2*v3 + v4) / 6. * particle.dt


def AdvectionRK4_3D(particle, fieldset, time):
    """Advection of particles using fourth-order Runge-Kutta integration including vertical velocity.

    Function needs to be converted to Kernel object before execution"""
    (u1, v1, w1) = fieldset.UVW[particle]
    lon1 = particle.lon + u1*.5*particle.dt
    lat1 = particle.lat + v1*.5*particle.dt
    dep1 = particle.depth + w1*.5*particle.dt
    (u2, v2, w2) = fieldset.UVW[time + .5 * particle.dt, dep1, lat1, lon1, particle]
    lon2 = particle.lon + u2*.5*particle.dt
    lat2 = particle.lat + v2*.5*particle.dt
    dep2 = particle.depth + w2*.5*particle.dt
    (u3, v3, w3) = fieldset.UVW[time + .5 * particle.dt, dep2, lat2, lon2, particle]
    lon3 = particle.lon + u3*particle.dt
    lat3 = particle.lat + v3*particle.dt
    dep3 = particle.depth + w3*particle.dt
    (u4, v4, w4) = fieldset.UVW[time + particle.dt, dep3, lat3, lon3, particle]
    particle.lon += (u1 + 2*u2 + 2*u3 + u4) / 6. * particle.dt
    particle.lat += (v1 + 2*v2 + 2*v3 + v4) / 6. * particle.dt
    particle.depth += (w1 + 2*w2 + 2*w3 + w4) / 6. * particle.dt


def AdvectionEE(particle, fieldset, time):
    """Advection of particles using Explicit Euler (aka Euler Forward) integration.

    Function needs to be converted to Kernel object before execution"""
    (u1, v1) = fieldset.UV[particle]
    particle.lon += u1 * particle.dt
    particle.lat += v1 * particle.dt


def AdvectionRK45(particle, fieldset, time):
    """Advection of particles using adadptive Runge-Kutta 4/5 integration.

    Times-step dt is halved if error is larger than tolerance, and doubled
    if error is smaller than 1/10th of tolerance, with tolerance set to
    1e-5 * dt by default."""
    rk45tol = 1e-5
    c = [1./4., 3./8., 12./13., 1., 1./2.]
    A = [[1./4., 0., 0., 0., 0.],
         [3./32., 9./32., 0., 0., 0.],
         [1932./2197., -7200./2197., 7296./2197., 0., 0.],
         [439./216., -8., 3680./513., -845./4104., 0.],
         [-8./27., 2., -3544./2565., 1859./4104., -11./40.]]
    b4 = [25./216., 0., 1408./2565., 2197./4104., -1./5.]
    b5 = [16./135., 0., 6656./12825., 28561./56430., -9./50., 2./55.]

    (u1, v1) = fieldset.UV[particle]
    lon1, lat1 = (particle.lon + u1 * A[0][0] * particle.dt,
                  particle.lat + v1 * A[0][0] * particle.dt)
    (u2, v2) = fieldset.UV[time + c[0] * particle.dt, particle.depth, lat1, lon1, particle]
    lon2, lat2 = (particle.lon + (u1 * A[1][0] + u2 * A[1][1]) * particle.dt,
                  particle.lat + (v1 * A[1][0] + v2 * A[1][1]) * particle.dt)
    (u3, v3) = fieldset.UV[time + c[1] * particle.dt, particle.depth, lat2, lon2, particle]
    lon3, lat3 = (particle.lon + (u1 * A[2][0] + u2 * A[2][1] + u3 * A[2][2]) * particle.dt,
                  particle.lat + (v1 * A[2][0] + v2 * A[2][1] + v3 * A[2][2]) * particle.dt)
    (u4, v4) = fieldset.UV[time + c[2] * particle.dt, particle.depth, lat3, lon3, particle]
    lon4, lat4 = (particle.lon + (u1 * A[3][0] + u2 * A[3][1] + u3 * A[3][2] + u4 * A[3][3]) * particle.dt,
                  particle.lat + (v1 * A[3][0] + v2 * A[3][1] + v3 * A[3][2] + v4 * A[3][3]) * particle.dt)
    (u5, v5) = fieldset.UV[time + c[3] * particle.dt, particle.depth, lat4, lon4, particle]
    lon5, lat5 = (particle.lon + (u1 * A[4][0] + u2 * A[4][1] + u3 * A[4][2] + u4 * A[4][3] + u5 * A[4][4]) * particle.dt,
                  particle.lat + (v1 * A[4][0] + v2 * A[4][1] + v3 * A[4][2] + v4 * A[4][3] + v5 * A[4][4]) * particle.dt)
    (u6, v6) = fieldset.UV[time + c[4] * particle.dt, particle.depth, lat5, lon5, particle]

    lon_4th = particle.lon + (u1 * b4[0] + u2 * b4[1] + u3 * b4[2] + u4 * b4[3] + u5 * b4[4]) * particle.dt
    lat_4th = particle.lat + (v1 * b4[0] + v2 * b4[1] + v3 * b4[2] + v4 * b4[3] + v5 * b4[4]) * particle.dt
    lon_5th = particle.lon + (u1 * b5[0] + u2 * b5[1] + u3 * b5[2] + u4 * b5[3] + u5 * b5[4] + u6 * b5[5]) * particle.dt
    lat_5th = particle.lat + (v1 * b5[0] + v2 * b5[1] + v3 * b5[2] + v4 * b5[3] + v5 * b5[4] + v6 * b5[5]) * particle.dt

    kappa2 = math.pow(lon_5th - lon_4th, 2) + math.pow(lat_5th - lat_4th, 2)
    if kappa2 <= math.pow(math.fabs(particle.dt * rk45tol), 2):
        particle.lon = lon_4th
        particle.lat = lat_4th
        if kappa2 <= math.pow(math.fabs(particle.dt * rk45tol / 10), 2):
            particle.update_next_dt(particle.dt * 2)
    else:
        particle.dt /= 2
        return OperationCode.Repeat


def AdvectionAnalytical(particle, fieldset, time):
    """Advection of particles using 'analytical advection' integration

    Based on Ariane/TRACMASS algorithm, as detailed in e.g. Doos et al (https://doi.org/10.5194/gmd-10-1733-2017).
    Note that the time-dependent scheme is currently implemented with 'intermediate timesteps'
    (default 10 per model timestep) and not yet with the full analytical time integration"""
    import numpy as np
    import parcels.tools.interpolation_utils as i_u

    tol = 1e-10
    I_s = 10  # number of intermediate time steps
    direction = 1. if particle.dt > 0 else -1.
    withW = True if 'W' in [f.name for f in fieldset.get_fields()] else False
    withTime = True if len(fieldset.U.grid.time_full) > 1 else False
    ti = fieldset.U.time_index(time)[0]
    ds_t = particle.dt
    if withTime:
        tau = (time - fieldset.U.grid.time[ti]) / (fieldset.U.grid.time[ti+1] - fieldset.U.grid.time[ti])
        time_i = np.linspace(0, fieldset.U.grid.time[ti+1] - fieldset.U.grid.time[ti], I_s)
        ds_t = min(ds_t, time_i[np.where(time - fieldset.U.grid.time[ti] < time_i)[0][0]])

    xsi, eta, zeta, xi, yi, zi = fieldset.U.search_indices(particle.lon, particle.lat, particle.depth, particle=particle)
    if withW:
        if abs(xsi - 1) < tol:
            if fieldset.U.data[0, zi+1, yi+1, xi+1] > 0:
                xi += 1
                xsi = 0
        if abs(eta - 1) < tol:
            if fieldset.V.data[0, zi+1, yi+1, xi+1] > 0:
                yi += 1
                eta = 0
        if abs(zeta - 1) < tol:
            if fieldset.W.data[0, zi+1, yi+1, xi+1] > 0:
                zi += 1
                zeta = 0
    else:
        if abs(xsi - 1) < tol:
            if fieldset.U.data[0, yi+1, xi+1] > 0:
                xi += 1
                xsi = 0
        if abs(eta - 1) < tol:
            if fieldset.V.data[0, yi+1, xi+1] > 0:
                yi += 1
                eta = 0

    particle.xi[:] = xi
    particle.yi[:] = yi
    particle.zi[:] = zi

    grid = fieldset.U.grid
    if grid.gtype < 2:
        px = np.array([grid.lon[xi], grid.lon[xi + 1], grid.lon[xi + 1], grid.lon[xi]])
        py = np.array([grid.lat[yi], grid.lat[yi], grid.lat[yi + 1], grid.lat[yi + 1]])
    else:
        px = np.array([grid.lon[yi, xi], grid.lon[yi, xi + 1], grid.lon[yi + 1, xi + 1], grid.lon[yi + 1, xi]])
        py = np.array([grid.lat[yi, xi], grid.lat[yi, xi + 1], grid.lat[yi + 1, xi + 1], grid.lat[yi + 1, xi]])
    if grid.mesh == 'spherical':
        px[0] = px[0]+360 if px[0] < particle.lon-225 else px[0]
        px[0] = px[0]-360 if px[0] > particle.lat+225 else px[0]
        px[1:] = np.where(px[1:] - px[0] > 180, px[1:]-360, px[1:])
        px[1:] = np.where(-px[1:] + px[0] > 180, px[1:]+360, px[1:])
    if withW:
        pz = np.array([grid.depth[zi], grid.depth[zi+1]])
        dz = pz[1] - pz[0]
    else:
        dz = 1.

    c1 = fieldset.UV.dist(px[0], px[1], py[0], py[1], grid.mesh, np.dot(i_u.phi2D_lin(xsi, 0.), py))
    c2 = fieldset.UV.dist(px[1], px[2], py[1], py[2], grid.mesh, np.dot(i_u.phi2D_lin(1., eta), py))
    c3 = fieldset.UV.dist(px[2], px[3], py[2], py[3], grid.mesh, np.dot(i_u.phi2D_lin(xsi, 1.), py))
    c4 = fieldset.UV.dist(px[3], px[0], py[3], py[0], grid.mesh, np.dot(i_u.phi2D_lin(0., eta), py))
    rad = np.pi / 180.
    deg2m = 1852 * 60.
    meshJac = (deg2m * deg2m * math.cos(rad * particle.lat)) if grid.mesh == 'spherical' else 1
    dxdy = fieldset.UV.jacobian(xsi, eta, px, py) * meshJac

    if withW:
        U0 = direction * fieldset.U.data[ti, zi+1, yi+1, xi] * c4 * dz
        U1 = direction * fieldset.U.data[ti, zi+1, yi+1, xi+1] * c2 * dz
        V0 = direction * fieldset.V.data[ti, zi+1, yi, xi+1] * c1 * dz
        V1 = direction * fieldset.V.data[ti, zi+1, yi+1, xi+1] * c3 * dz
        if withTime:
            U0 = U0 * (1 - tau) + tau * direction * fieldset.U.data[ti+1, zi+1, yi+1, xi] * c4 * dz
            U1 = U1 * (1 - tau) + tau * direction * fieldset.U.data[ti+1, zi+1, yi+1, xi+1] * c2 * dz
            V0 = V0 * (1 - tau) + tau * direction * fieldset.V.data[ti+1, zi+1, yi, xi+1] * c1 * dz
            V1 = V1 * (1 - tau) + tau * direction * fieldset.V.data[ti+1, zi+1, yi+1, xi+1] * c3 * dz
    else:
        U0 = direction * fieldset.U.data[ti, yi+1, xi] * c4 * dz
        U1 = direction * fieldset.U.data[ti, yi+1, xi+1] * c2 * dz
        V0 = direction * fieldset.V.data[ti, yi, xi+1] * c1 * dz
        V1 = direction * fieldset.V.data[ti, yi+1, xi+1] * c3 * dz
        if withTime:
            U0 = U0 * (1 - tau) + tau * direction * fieldset.U.data[ti+1, yi+1, xi] * c4 * dz
            U1 = U1 * (1 - tau) + tau * direction * fieldset.U.data[ti+1, yi+1, xi+1] * c2 * dz
            V0 = V0 * (1 - tau) + tau * direction * fieldset.V.data[ti+1, yi, xi+1] * c1 * dz
            V1 = V1 * (1 - tau) + tau * direction * fieldset.V.data[ti+1, yi+1, xi+1] * c3 * dz

    def compute_ds(F0, F1, r, direction, tol):
        up = F0 * (1-r) + F1 * r
        r_target = 1. if direction * up >= 0. else 0.
        B = F0 - F1
        delta = - F0
        B = 0 if abs(B) < tol else B

        if abs(B) > tol:
            F_r1 = r_target + delta / B
            F_r0 = r + delta / B
        else:
            F_r0, F_r1 = None, None

        if abs(B) < tol and abs(delta) < tol:
            ds = float('inf')
        elif B == 0:
            ds = -(r_target - r) / delta
        elif F_r1 * F_r0 < tol:
            ds = float('inf')
        else:
            ds = - 1. / B * math.log(F_r1 / F_r0)

        if abs(ds) < tol:
            ds = float('inf')
        return ds, B, delta

    ds_x, B_x, delta_x = compute_ds(U0, U1, xsi, direction, tol)
    ds_y, B_y, delta_y = compute_ds(V0, V1, eta, direction, tol)
    if withW:
        W0 = direction * fieldset.W.data[ti, zi, yi+1, xi+1] * dxdy
        W1 = direction * fieldset.W.data[ti, zi+1, yi+1, xi+1] * dxdy
        if withTime:
            W0 = W0 * (1 - tau) + tau * direction * fieldset.W.data[ti+1, zi, yi + 1, xi + 1] * dxdy
            W1 = W1 * (1 - tau) + tau * direction * fieldset.W.data[ti+1, zi + 1, yi + 1, xi + 1] * dxdy
        ds_z, B_z, delta_z = compute_ds(W0, W1, zeta, direction, tol)
    else:
        ds_z = float('inf')

    # take the minimum travel time
    s_min = min(abs(ds_x), abs(ds_y), abs(ds_z), abs(ds_t / (dxdy * dz)))

    # calculate end position in time s_min
    def compute_rs(ds, r, B, delta, s_min):
        if abs(B) < tol:
            return -delta * s_min + r
        else:
            return (r + delta / B) * math.exp(-B * s_min) - delta / B

    rs_x = compute_rs(ds_x, xsi, B_x, delta_x, s_min)
    rs_y = compute_rs(ds_y, eta, B_y, delta_y, s_min)

    particle.lon = (1.-rs_x)*(1.-rs_y) * px[0] + rs_x * (1.-rs_y) * px[1] + rs_x * rs_y * px[2] + (1.-rs_x)*rs_y * px[3]
    particle.lat = (1.-rs_x)*(1.-rs_y) * py[0] + rs_x * (1.-rs_y) * py[1] + rs_x * rs_y * py[2] + (1.-rs_x)*rs_y * py[3]

    if withW:
        rs_z = compute_rs(ds_z, zeta, B_z, delta_z, s_min)
        particle.depth = (1.-rs_z) * pz[0] + rs_z * pz[1]

    # update the passed time for the main loop
    particle.dt = direction * s_min * (dxdy * dz)
