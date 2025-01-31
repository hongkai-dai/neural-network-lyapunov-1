import neural_network_lyapunov.train_barrier as train_barrier
import neural_network_lyapunov.barrier as barrier
import neural_network_lyapunov.control_barrier as control_barrier
import neural_network_lyapunov.examples.quadrotor2d.control_affine_quadrotor\
    as control_affine_quadrotor
import neural_network_lyapunov.examples.quadrotor2d.quadrotor_2d as \
    quadrotor_2d
import neural_network_lyapunov.utils as utils
import neural_network_lyapunov.gurobi_torch_mip as gurobi_torch_mip
import neural_network_lyapunov.mip_utils as mip_utils
import neural_network_lyapunov.integrator as integrator
import neural_network_lyapunov.nominal_controller as nominal_controller

import torch
import argparse
import numpy as np
import gurobipy
import matplotlib.pyplot as plt


def simulate(dynamics_model: control_affine_quadrotor.ControlAffineQuadrotor2d,
             barrier_relu, x_star, c, inf_norm_term, u_lo, u_up, epsilon, x0,
             pos_des, T):
    """
    Simulate the system to go to a desired hovering position, while respecting
    the barrier cerificate.
    """
    dtype = torch.float64
    plant = quadrotor_2d.Quadrotor2D(dtype)
    dut = control_barrier.ControlBarrier(dynamics_model, barrier_relu)

    x_des = np.array([pos_des[0], pos_des[1], 0, 0, 0, 0])
    h_des = barrier_relu(torch.from_numpy(x_des)) - barrier_relu(x_star) + c
    if h_des < 0:
        raise Exception(f"The desired state has h value = {h_des.item()} < 0")
    u_des = np.array([1, 1]) * plant.mass * plant.gravity * 0.5

    A, B = plant.linearized_dynamics(x_des, u_des)
    Q = np.diag([1, 1, 1, 10, 10, 10])
    R = np.diag([1, 1.])
    K, S = plant.lqr_control(Q, R, x_des, u_des)

    def compute_control(x):
        prog = gurobipy.Model()
        u = prog.addVars(2, lb=u_lo.tolist(), ub=u_up.tolist())
        u_var = [u[0], u[1]]
        x_torch = torch.from_numpy(x)
        with torch.no_grad():
            dhdx = dut._barrier_gradient(x_torch, inf_norm_term)

            f = dynamics_model.f(x_torch)
            G = dynamics_model.G(x_torch)
            h = dut.barrier_value(x_torch,
                                  x_star,
                                  c,
                                  inf_norm_term=inf_norm_term)
            for i in range(dhdx.shape[0]):
                prog.addLConstr(gurobipy.LinExpr((dhdx[i] @ G).tolist(),
                                                 u_var),
                                sense=gurobipy.GRB.GREATER_EQUAL,
                                rhs=(-epsilon * h - dhdx[i] @ f).item())
            # Add the cost
            # min (u-u_des)ᵀR(u-u_des) + 2(x-x_des)ᵀS(A(x-x_des)+Bu)
            cost = gurobipy.QuadExpr()
            cost.add((u[0] - u_des[0]) * (u[0] - u_des[0]) * R[0, 0] +
                     (u[1] - u_des[1]) * (u[1] - u_des[1]) * R[1, 1] + 2 *
                     (u[0] - u_des[0]) * (u[1] - u_des[1]) * R[0, 1])
            cost.addTerms(2 * (x - x_des) @ S @ B, u_var)
            prog.setObjective(cost, sense=gurobipy.GRB.MINIMIZE)
            prog.setParam(gurobipy.GRB.Param.OutputFlag, False)
            prog.optimize()
            assert (prog.status == gurobipy.GRB.Status.OPTIMAL)
            u_val = np.array([v.x for v in u_var])
            return u_val

    def plant_dynamics(t, x):
        u = compute_control(x)
        return plant.dynamics(x, u)

    def nn_plant_dynamics(t, x):
        u = compute_control(x)
        with torch.no_grad():
            return dynamics_model.dynamics(torch.from_numpy(x),
                                           torch.from_numpy(u))

    dt = 0.001
    constant_control_steps = 1
    num_control_cycles = int(T / (dt * constant_control_steps))
    u_val = np.zeros((2, num_control_cycles))
    x_val = np.zeros((6, num_control_cycles))
    x_val[:, 0] = x0
    hdot = np.zeros((num_control_cycles, ))
    with torch.no_grad():
        for i in range(num_control_cycles - 1):
            x_val[:, i + 1], u_val[:, i] = integrator.rk4_constant_control(
                lambda x, u: dynamics_model.dynamics(torch.from_numpy(
                    x), torch.from_numpy(u)).detach().numpy(), compute_control,
                x_val[:, i], dt, constant_control_steps)
        u_val[:, -1] = u_val[:, -2]
        for i in range(num_control_cycles):
            hdot[i] = dut.minimal_barrier_derivative_given_action(
                torch.from_numpy(x_val[:, i]),
                torch.from_numpy(u_val[:, i]),
                inf_norm_term=inf_norm_term)
        h_val = dut.barrier_value(torch.from_numpy(x_val.T),
                                  x_star,
                                  c,
                                  inf_norm_term=inf_norm_term)

    t_samples = np.arange(num_control_cycles) * dt * constant_control_steps
    fig_u = plt.figure()
    ax_u0 = fig_u.add_subplot(211)
    ax_u0.plot(t_samples, u_val[0, :])
    ax_u0.set_title("u")
    ax_u1 = fig_u.add_subplot(212)
    ax_u1.plot(t_samples, u_val[1, :])
    ax_u1.set_xlabel("time (s)")

    fig_h = plt.figure()
    ax_h0 = fig_h.add_subplot(211)
    ax_h0.plot(t_samples, h_val)
    ax_h0.set_title("h")
    ax_h1 = fig_h.add_subplot(212)
    ax_h1.plot(t_samples, hdot)
    ax_h1.set_xlabel("time (s)")

    fig_u.show()
    fig_h.show()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="quadrotor2d cbf training demo")
    parser.add_argument("--load_forward_model",
                        type=str,
                        default=None,
                        help="path to load the forward model")
    parser.add_argument("--load_barrier_relu",
                        type=str,
                        default=None,
                        help="path to load the control barrier model")
    parser.add_argument("--train_on_samples", action="store_true")
    parser.add_argument("--max_iterations", type=int, default=1000)
    parser.add_argument("--enable_wandb", action="store_true")
    parser.add_argument("--simulate", action="store_true")
    args = parser.parse_args()
    dtype = torch.float64

    plant = quadrotor_2d.Quadrotor2D(dtype)
    x_lo = torch.tensor([-0.5, -0.5, -0.3 * np.pi, -3, -3, -1.5], dtype=dtype)
    x_up = -x_lo
    u_lo = torch.tensor([0, 0], dtype=dtype)
    u_up = torch.tensor([1, 1], dtype=dtype) * plant.mass * plant.gravity * 1.5
    dynamics_model_data = torch.load(args.load_forward_model)
    phi_b = utils.setup_relu(
        dynamics_model_data["phi_b"]["linear_layer_width"],
        params=None,
        negative_slope=dynamics_model_data["phi_b"]["negative_slope"],
        bias=dynamics_model_data["phi_b"]["bias"],
        dtype=dtype)
    phi_b.load_state_dict(dynamics_model_data["phi_b"]["state_dict"])
    u_equilibrium = torch.tensor([0.5, 0.5],
                                 dtype=dtype) * plant.mass * plant.gravity
    dynamics_model = control_affine_quadrotor.ControlAffineQuadrotor2d(
        x_lo,
        x_up,
        u_lo,
        u_up,
        phi_b,
        u_equilibrium,
        method=mip_utils.PropagateBoundsMethod.IA)
    x_equilibrium = torch.zeros((6, ), dtype=dtype)

    if args.load_barrier_relu is None:
        barrier_relu = utils.setup_relu((6, 15, 15, 1),
                                        params=None,
                                        negative_slope=0.1,
                                        bias=True,
                                        dtype=dtype)
        c = 0.5
        x_star = x_equilibrium
        barrier_system = control_barrier.ControlBarrier(
            dynamics_model, barrier_relu)
        nominal_controller_nn = utils.setup_relu((6, 4, 3, 2),
                                                 params=None,
                                                 negative_slope=0.1,
                                                 bias=True,
                                                 dtype=dtype)
    else:
        barrier_data = torch.load(args.load_barrier_relu)
        barrier_relu = utils.setup_relu(
            barrier_data["linear_layer_width"],
            params=None,
            negative_slope=barrier_data["negative_slope"],
            bias=True)
        barrier_relu.load_state_dict(barrier_data["state_dict"])
        x_star = barrier_data["x_star"]
        c = barrier_data["c"]
        barrier_system = control_barrier.ControlBarrier(
            dynamics_model, barrier_relu)
        nominal_controller_nn = utils.setup_relu(
            barrier_data["nominal_control"]["linear_layer_width"],
            params=None,
            negative_slope=barrier_data["nominal_control"]["negative_slope"],
            bias=barrier_data["nominal_control"]["bias"],
            dtype=dtype)
        nominal_controller_nn.load_state_dict(
            barrier_data["nominal_control"]["state_dict"])

    # The unsafe region is z < -0.2
    unsafe_region_cnstr = gurobi_torch_mip.MixedIntegerConstraintsReturn()
    unsafe_height = -0.2
    # unsafe_region_cnstr.Ain_input = torch.tensor([[0, 1, 0, 0, 0, 0]],
    #                                              dtype=dtype)
    # unsafe_region_cnstr.rhs_in = torch.tensor([unsafe_height], dtype=dtype)

    verify_region_boundary = utils.box_boundary(x_lo, x_up)

    epsilon = 0.3

    inf_norm_term = barrier.InfNormTerm(torch.diag(2. / (x_up - x_lo)),
                                        (x_up + x_lo) / (x_up - x_lo))

    nominal_control_state_samples = utils.uniform_sample_in_box(
        x_lo, x_up, 30000)
    u_star = torch.ones((2, ), dtype=dtype) * plant.mass * plant.gravity / 2
    nominal_control_option = train_barrier.NominalControlOption(
        nominal_controller.NominalNNController(nominal_controller_nn, x_star,
                                               u_star, u_lo, u_up),
        nominal_control_state_samples,
        weight=10.,
        margin=0.5,
        norm="mean",
        nominal_control_loss_tol=0.8)

    dut = train_barrier.TrainBarrier(barrier_system, x_star, c,
                                     unsafe_region_cnstr,
                                     verify_region_boundary, epsilon,
                                     inf_norm_term, nominal_control_option)
    dut.max_iterations = args.max_iterations
    dut.enable_wandb = args.enable_wandb

    if args.simulate:
        simulate(dynamics_model, barrier_relu, x_star, c, inf_norm_term, u_lo,
                 u_up, epsilon, np.zeros((6, )), np.array([0, -0.35]), 8)
    else:
        if args.train_on_samples:
            # First train on samples without solving MIP.
            dut.derivative_state_samples_weight = 1.
            dut.boundary_state_samples_weight = 1.
            dut.unsafe_state_samples_weight = 1.
            x_up_unsafe = x_up.clone()
            x_up_unsafe[1] = unsafe_height
            # unsafe_state_samples = utils.uniform_sample_in_box(
            #     x_lo, x_up_unsafe, 1000)
            unsafe_state_samples = torch.empty((0, 6), dtype=dtype)
            boundary_state_samples = utils.uniform_sample_on_box_boundary(
                x_lo, x_up, 3000)
            deriv_state_samples = utils.uniform_sample_in_box(x_lo, x_up, 2000)
            dut.train_on_samples(unsafe_state_samples, boundary_state_samples,
                                 deriv_state_samples)
            pass
        unsafe_state_samples = torch.zeros((0, 6), dtype=dtype)
        boundary_state_samples = torch.zeros((0, 6), dtype=dtype)
        deriv_state_samples = torch.zeros((0, 6), dtype=dtype)
        dut.deriv_mip_margin = 0.5
        # dut.verify_region_boundary_mip_cost_weight = 5
        dut.train(unsafe_state_samples, boundary_state_samples,
                  deriv_state_samples)
    pass
