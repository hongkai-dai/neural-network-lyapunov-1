import gurobipy
import numpy as np
import unittest
import torch
import torch.nn as nn

import neural_network_lyapunov.lyapunov as lyapunov
import neural_network_lyapunov.relu_to_optimization as relu_to_optimization
import neural_network_lyapunov.gurobi_torch_mip as gurobi_torch_mip
import neural_network_lyapunov.hybrid_linear_system as hybrid_linear_system
import neural_network_lyapunov.relu_system as relu_system
import neural_network_lyapunov.feedback_system as feedback_system
import neural_network_lyapunov.utils as utils
import neural_network_lyapunov.mip_utils as mip_utils
import neural_network_lyapunov.test.test_hybrid_linear_system as\
    test_hybrid_linear_system
import neural_network_lyapunov.compute_xhat as compute_xhat


def setup_relu(dtype, params=None):
    # Construct a simple ReLU model with 2 hidden layers
    # params is the value of weights/bias after concatenation.
    if params is not None:
        assert (isinstance(params, torch.Tensor))
        assert (params.shape == (30, ))
    linear1 = nn.Linear(2, 3)
    if params is None:
        linear1.weight.data = torch.tensor([[1, 2], [3, 4], [5, 6]],
                                           dtype=dtype)
        linear1.bias.data = torch.tensor([-11, 10, 5], dtype=dtype)
    else:
        linear1.weight.data = params[:6].clone().reshape((3, 2))
        linear1.bias.data = params[6:9].clone()
    linear2 = nn.Linear(3, 4)
    if params is None:
        linear2.weight.data = torch.tensor(
            [[-1, -0.5, 1.5], [2, 5, 6], [-2, -3, -4], [1.5, 4, 6]],
            dtype=dtype)
        linear2.bias.data = torch.tensor([-3, 2, 0.7, 1.5], dtype=dtype)
    else:
        linear2.weight.data = params[9:21].clone().reshape((4, 3))
        linear2.bias.data = params[21:25].clone()
    linear3 = nn.Linear(4, 1)
    if params is None:
        linear3.weight.data = torch.tensor([[4, 5, 6, 7]], dtype=dtype)
        linear3.bias.data = torch.tensor([-9], dtype=dtype)
    else:
        linear3.weight.data = params[25:29].clone().reshape((1, 4))
        linear3.bias.data = params[29].clone().reshape((1))
    relu1 = nn.Sequential(linear1, nn.ReLU(), linear2, nn.ReLU(), linear3)
    assert (not relu1.forward(torch.tensor([0, 0], dtype=dtype)).item() == 0)
    return relu1


def setup_leaky_relu(dtype, params=None, bias=True):
    if params is not None and bias:
        assert (isinstance(params, torch.Tensor))
        assert (params.shape == (30, ))
    linear1 = nn.Linear(2, 3, bias=bias)
    param_count = 0
    if params is None:
        linear1.weight.data = torch.tensor(
            [[-1.3405, -0.2602], [-0.9392, 0.9033], [-2.1063, 1.3141]],
            dtype=dtype)
        if bias:
            linear1.bias.data = torch.tensor([0.913, 0.6429, 0.0011],
                                             dtype=dtype)
    else:
        linear1.weight.data = params[:6].clone().reshape((3, 2))
        param_count += 6
        if bias:
            linear1.bias.data = params[param_count:param_count + 3].clone()
            param_count += 3
    linear2 = nn.Linear(3, 4, bias=bias)
    if params is None:
        linear2.weight.data = torch.tensor(
            [[-0.4209, -1.1947, 1.4353], [1.7519, -1.3908, 2.6274],
             [-2.7574, 0.3764, -0.5544], [-0.3721, -1.0413, 0.52]],
            dtype=dtype)
        if bias:
            linear2.bias.data = torch.tensor([-0.9802, 1.1129, 1.0941, 1.582],
                                             dtype=dtype)
    else:
        linear2.weight.data = params[param_count: param_count + 12].clone().\
            reshape((4, 3))
        param_count += 12
        if bias:
            linear2.bias.data = params[param_count:param_count + 4].clone()
            param_count += 4
    linear3 = nn.Linear(4, 1)
    if params is None:
        linear3.weight.data = torch.tensor([[-1.1727, 0.2846, 1.2452, 0.8230]],
                                           dtype=dtype)
        linear3.bias.data = torch.tensor([0.4431], dtype=dtype)
    else:
        linear3.weight.data = params[param_count:param_count + 4].clone().\
            reshape((1, 4))
        param_count += 4
        if bias:
            linear3.bias.data = params[param_count].clone().reshape((1))
            param_count += 1
    relu = nn.Sequential(linear1, nn.LeakyReLU(0.1), linear2,
                         nn.LeakyReLU(0.1), linear3)
    return relu


def setup_relu_dyn(dtype, params=None):
    # Construct a simple ReLU model with 2 hidden layers
    # params is the value of weights/bias after concatenation.
    # the network has the same number of outputs as inputs (2)
    if params is not None:
        assert (isinstance(params, torch.Tensor))
        assert (params.shape == (35, ))
    linear1 = nn.Linear(2, 3)
    if params is None:
        linear1.weight.data = torch.tensor(
            [[.01, .02], [.03, .04], [.05, .06]], dtype=dtype)
        linear1.bias.data = torch.tensor([0., 0., 0.], dtype=dtype)
    else:
        linear1.weight.data = params[:6].clone().reshape((3, 2))
        linear1.bias.data = params[6:9].clone()
    linear2 = nn.Linear(3, 4)
    if params is None:
        linear2.weight.data = torch.tensor(
            [[-.1, -0.05, .15], [.2, .5, .6], [-.2, -.3, -.4], [.15, .4, .6]],
            dtype=dtype)
        linear2.bias.data = torch.tensor([0., 0., 0., 0.], dtype=dtype)
    else:
        linear2.weight.data = params[9:21].clone().reshape((4, 3))
        linear2.bias.data = params[21:25].clone()
    linear3 = nn.Linear(4, 2)
    if params is None:
        linear3.weight.data = torch.tensor(
            [[.4, .5, .6, .7], [.8, .7, 0.5, 0.5]], dtype=dtype)
        linear3.bias.data = torch.tensor([0.0, 0.0], dtype=dtype)
    else:
        linear3.weight.data = params[25:33].clone().reshape((2, 4))
        linear3.bias.data = params[33:35].clone().reshape((2))
    relu1 = nn.Sequential(linear1, nn.ReLU(), linear2, nn.ReLU(), linear3)
    return relu1


def setup_relu_dyn_3d(dtype):
    torch.manual_seed(0)
    x_equilibrium = torch.tensor([0.5, 0.3, -1.1], dtype=dtype)
    dynamics_relu = utils.setup_relu((3, 5, 3),
                                     params=None,
                                     negative_slope=0.1,
                                     bias=True,
                                     dtype=dtype)
    system = relu_system.AutonomousReLUSystemGivenEquilibrium(
        dtype, torch.tensor([-2, -4, -5], dtype=dtype),
        torch.tensor([3, 8, 1], dtype=dtype), dynamics_relu, x_equilibrium)
    return system


def setup_hybrid_feedback_system(dtype):
    # Setup a feedback system with HybridLinearSystem as the forward system,
    # and a neural network as the controller.
    # The forward system is
    # x[n+1] = 0.9 * x[n] + u[n] if
    # [-10;-10] <= x[n] <= [0, 10], [-10, -10] <= u[n] <= [10, 10]
    # x[n+1] = 1.1 * x[n] - u[n] if
    # [0, -10] <= x[n] <= [10, 10], [-10, -10] <= u[n] <= [10, 10]
    forward_system = hybrid_linear_system.HybridLinearSystem(2, 2, dtype)
    P = torch.zeros(8, 4, dtype=dtype)
    P[:2, :2] = torch.eye(2, dtype=dtype)
    P[2:4, :2] = -torch.eye(2, dtype=dtype)
    P[4:6, 2:] = torch.eye(2, dtype=dtype)
    P[6:, 2:] = -torch.eye(2, dtype=dtype)
    forward_system.add_mode(
        0.9 * torch.eye(2, dtype=dtype), torch.eye(2, dtype=dtype),
        torch.zeros(2, dtype=dtype), P,
        torch.tensor([0, 10, 10, 10, 10, 10, 10, 10], dtype=dtype))
    forward_system.add_mode(
        1.1 * torch.eye(2, dtype=dtype), -torch.eye(2, dtype=dtype),
        torch.zeros(2, dtype=dtype), P,
        torch.tensor([10, 10, 0, 10, 10, 10, 10, 10], dtype=dtype))
    # setup a neural network for controller
    linear1 = torch.nn.Linear(2, 2)
    linear1.weight.data = torch.tensor([[0.1, 0.3], [-0.6, 1.1]], dtype=dtype)
    linear1.bias.data = torch.tensor([1.2, 0.1], dtype=dtype)
    linear2 = torch.nn.Linear(2, 2)
    linear2.weight.data = torch.tensor([[0.3, 0.8], [-0.5, 0.4]], dtype=dtype)
    linear2.bias.data = torch.tensor([0.1, 0.3], dtype=dtype)
    controller_network = torch.nn.Sequential(linear1, torch.nn.LeakyReLU(0.01),
                                             linear2)
    x_equilibrium = torch.tensor([1, 1], dtype=dtype)
    u_equilibrium = torch.tensor([0.1, 0.1], dtype=dtype)
    u_lower_limit = np.array([-np.inf, -np.inf])
    u_upper_limit = np.array([np.inf, np.inf])
    system = feedback_system.FeedbackSystem(forward_system, controller_network,
                                            x_equilibrium, u_equilibrium,
                                            u_lower_limit, u_upper_limit)
    return system


class TestLyapunovHybridSystem(unittest.TestCase):
    def setUp(self):
        self.dtype = torch.float64
        self.x_equilibrium1 = torch.tensor([0, 0], dtype=self.dtype)
        self.system1 = test_hybrid_linear_system.\
            setup_trecate_discrete_time_system()
        self.theta2 = np.pi / 5
        cos_theta2 = np.cos(self.theta2)
        sin_theta2 = np.sin(self.theta2)
        self.R2 = torch.tensor(
            [[cos_theta2, -sin_theta2], [sin_theta2, cos_theta2]],
            dtype=self.dtype)
        self.x_equilibrium2 = torch.tensor([0.4, 2.5], dtype=self.dtype)
        self.system2 = \
            test_hybrid_linear_system.setup_transformed_trecate_system(
                self.theta2, self.x_equilibrium2)
        self.system3 = test_hybrid_linear_system.\
            setup_johansson_continuous_time_system1()
        self.x_equilibrium3 = torch.tensor([0, 0], dtype=self.dtype)

    def test_add_system_constraint(self):
        def test_fun(system, x_val, is_x_valid):
            milp = gurobi_torch_mip.GurobiTorchMILP(self.dtype)
            relu = setup_leaky_relu(self.dtype)
            dut = lyapunov.LyapunovHybridLinearSystem(system, relu)
            x = milp.addVars(system.x_dim,
                             lb=-gurobipy.GRB.INFINITY,
                             vtype=gurobipy.GRB.CONTINUOUS)
            mip_cnstr_return = system.mixed_integer_constraints()
            s, gamma = dut.add_system_constraint(milp, x, None)
            # Now fix x to x_val
            for i in range(system.x_dim):
                milp.addLConstr([torch.tensor([1.], dtype=self.dtype)],
                                [[x[i]]],
                                sense=gurobipy.GRB.EQUAL,
                                rhs=x_val[i])
            milp.gurobi_model.setParam(gurobipy.GRB.Param.OutputFlag, 0)
            milp.gurobi_model.setParam(gurobipy.GRB.Param.DualReductions, 0)
            milp.gurobi_model.optimize()
            if is_x_valid:
                self.assertEqual(milp.gurobi_model.status,
                                 gurobipy.GRB.OPTIMAL)
                mode_index = system.mode(x_val)
                gamma_expected = np.zeros(system.num_modes)
                gamma_expected[mode_index] = 1.
                s_expected = np.zeros(system.num_modes * system.x_dim)
                s_expected[mode_index * system.x_dim:
                           (mode_index+1) * system.x_dim] =\
                    x_val.detach().numpy()
                s_sol = np.array([v.x for v in s])
                np.testing.assert_allclose(s_sol, s_expected)
                gamma_sol = np.array([v.x for v in gamma])
                np.testing.assert_allclose(gamma_sol, gamma_expected)
                np.testing.assert_allclose(
                    mip_cnstr_return.Aout_slack @ torch.from_numpy(s_sol) +
                    mip_cnstr_return.Aout_binary @ torch.from_numpy(gamma_sol),
                    system.A[mode_index] @ x_val + system.g[mode_index])
            else:
                self.assertEqual(milp.gurobi_model.status,
                                 gurobipy.GRB.INFEASIBLE)

        test_fun(self.system1, torch.tensor([0.5, 0.2], dtype=self.dtype),
                 True)
        test_fun(self.system1, torch.tensor([-0.5, 0.2], dtype=self.dtype),
                 True)
        test_fun(self.system1, torch.tensor([-1.5, 0.2], dtype=self.dtype),
                 False)
        test_fun(
            self.system2,
            self.R2 @ torch.tensor([-0.5, 0.2], dtype=self.dtype) +
            self.x_equilibrium2, True)
        test_fun(
            self.system2,
            self.R2 @ torch.tensor([-0.5, 1.2], dtype=self.dtype) +
            self.x_equilibrium2, False)
        test_fun(self.system3, torch.tensor([-0.5, 0.7], dtype=self.dtype),
                 True)

    def test_add_lyap_relu_output_constraint(self):
        def test_fun(relu, system, x_val):
            relu_free_pattern = relu_to_optimization.ReLUFreePattern(
                relu, self.dtype)
            milp = gurobi_torch_mip.GurobiTorchMILP(self.dtype)
            x = milp.addVars(system.x_dim,
                             lb=-gurobipy.GRB.INFINITY,
                             vtype=gurobipy.GRB.CONTINUOUS)
            dut = lyapunov.LyapunovHybridLinearSystem(system, relu)
            (z, beta, a_out,
             b_out) = dut.add_lyap_relu_output_constraint(milp, x)
            for i in range(system.x_dim):
                milp.addLConstr([torch.tensor([1.], dtype=self.dtype)],
                                [[x[i]]],
                                sense=gurobipy.GRB.EQUAL,
                                rhs=x_val[i])
            milp.gurobi_model.setParam(gurobipy.GRB.Param.OutputFlag, 0)
            milp.gurobi_model.optimize()
            self.assertEqual(milp.gurobi_model.status, gurobipy.GRB.OPTIMAL)
            activation_pattern = \
                relu_to_optimization.ComputeReLUActivationPattern(relu, x_val)
            for i in range(len(relu_free_pattern.relu_unit_index)):
                for j in range(len(relu_free_pattern.relu_unit_index[i])):
                    self.assertAlmostEqual(
                        activation_pattern[i][j],
                        beta[relu_free_pattern.relu_unit_index[i][j]].x)
            self.assertAlmostEqual(
                (a_out @ torch.tensor([v.x for v in z], dtype=system.dtype) +
                 b_out).item(),
                relu.forward(x_val).item())

        lyapunov_relu1 = setup_relu(self.dtype)
        lyapunov_relu2 = setup_leaky_relu(self.dtype)
        test_fun(lyapunov_relu1, self.system1,
                 torch.tensor([0.5, 0.2], dtype=self.dtype))
        test_fun(lyapunov_relu2, self.system1,
                 torch.tensor([0.5, 0.2], dtype=self.dtype))
        test_fun(lyapunov_relu2, self.system1,
                 torch.tensor([-0.5, 0.2], dtype=self.dtype))

    def add_state_error_l1_constraint_tester(self, system, relu, x_equilibrium,
                                             x_val, R, xbar_indices):
        milp = gurobi_torch_mip.GurobiTorchMILP(self.dtype)
        x = milp.addVars(system.x_dim,
                         lb=-gurobipy.GRB.INFINITY,
                         vtype=gurobipy.GRB.CONTINUOUS)
        dut = lyapunov.LyapunovHybridLinearSystem(system, relu)
        s, alpha = dut.add_state_error_l1_constraint(milp,
                                                     x_equilibrium,
                                                     x,
                                                     R=R,
                                                     xbar_indices=xbar_indices)
        if xbar_indices is None:
            xbar_indices = list(range(system.x_dim))
        s_dim = R.shape[0]
        self.assertEqual(len(s), s_dim)
        self.assertEqual(len(alpha), s_dim)
        for i in xbar_indices:
            milp.addLConstr([torch.tensor([1.], dtype=system.dtype)], [[x[i]]],
                            sense=gurobipy.GRB.EQUAL,
                            rhs=x_val[i])
        milp.gurobi_model.setParam(gurobipy.GRB.Param.OutputFlag, 0)
        milp.gurobi_model.optimize()
        self.assertEqual(milp.gurobi_model.status, gurobipy.GRB.OPTIMAL)
        s_val = R @ (x_val[xbar_indices] - x_equilibrium[xbar_indices])
        for i in range(s_dim):
            if s_val[i] >= 0:
                self.assertAlmostEqual(alpha[i].x, 1)
                self.assertAlmostEqual(s[i].x, s_val[i].item())
            else:
                self.assertAlmostEqual(alpha[i].x, 0)
                self.assertAlmostEqual(s[i].x, -s_val[i].item())

    def test_add_state_error_l1_constraint(self):
        relu = setup_leaky_relu(self.dtype)
        R = torch.tensor([[1, 1], [-1, 1], [1, 0]], dtype=self.dtype)

        self.add_state_error_l1_constraint_tester(self.system1,
                                                  relu,
                                                  self.x_equilibrium1,
                                                  torch.tensor(
                                                      [0.5, -0.3],
                                                      dtype=self.dtype),
                                                  R,
                                                  xbar_indices=None)
        self.add_state_error_l1_constraint_tester(self.system1,
                                                  relu,
                                                  self.x_equilibrium1,
                                                  torch.tensor(
                                                      [-0.5, -0.3],
                                                      dtype=self.dtype),
                                                  R,
                                                  xbar_indices=None)
        self.add_state_error_l1_constraint_tester(
            self.system2,
            relu,
            self.x_equilibrium2,
            self.R2 @ torch.tensor([-0.5, -0.3], dtype=self.dtype) +
            self.x_equilibrium2,
            R,
            xbar_indices=None)
        self.add_state_error_l1_constraint_tester(
            self.system2,
            relu,
            self.x_equilibrium2,
            self.R2 @ torch.tensor([0.5, -0.3], dtype=self.dtype) +
            self.x_equilibrium2,
            R,
            xbar_indices=None)
        # system 5 has some x_lo equal to x_equilibrium.
        system5 = test_hybrid_linear_system.\
            setup_johansson_continuous_time_system5(keep_positive_x=True)
        self.add_state_error_l1_constraint_tester(
            system5,
            relu,
            torch.tensor([0., 0], dtype=self.dtype),
            torch.tensor([0.5, 0.3], dtype=self.dtype),
            R,
            xbar_indices=None)
        # system 6 has some x_up equal to x_equilibrium.
        system5_full = test_hybrid_linear_system.\
            setup_johansson_continuous_time_system5()
        system6 = hybrid_linear_system.AutonomousHybridLinearSystem(
            2, dtype=self.dtype)
        system6.add_mode(system5_full.A[1], system5_full.g[1],
                         system5_full.P[1], system5_full.q[1])
        system6.add_mode(system5_full.A[2], system5_full.g[2],
                         system5_full.P[2], system5_full.q[2])
        self.add_state_error_l1_constraint_tester(
            system6,
            relu,
            torch.tensor([0., 0], dtype=self.dtype),
            torch.tensor([-0.2, 0.3], dtype=self.dtype),
            R,
            xbar_indices=None)

    def test_add_state_error_l1_constraint_partial_state(self):
        torch.manual_seed(0)
        lyap_relu = utils.setup_relu((3, 5, 1),
                                     params=None,
                                     negative_slope=0.1,
                                     bias=True,
                                     dtype=torch.float64)
        system = setup_relu_dyn_3d(self.dtype)
        R = torch.tensor([[1, 4], [0, -1], [3, 2]], dtype=self.dtype)
        self.add_state_error_l1_constraint_tester(
            system,
            lyap_relu,
            x_equilibrium=system.x_equilibrium,
            x_val=torch.tensor([0.5, 0.4, 0.8], dtype=self.dtype),
            R=R,
            xbar_indices=[0, 2])

    def test_add_lyapunov_bounds_constraint(self):
        V_lambda = 0.5

        def test_fun(lyapunov_lower, lyapunov_upper, system, relu,
                     x_equilibrium, R, x_val, xhat_indices):
            """
            Set x = x_val, check if the MILP
            lyapunov_lower <= V(x) <= lyapunov_upper is feasible or not.
            """
            dut = lyapunov.LyapunovHybridLinearSystem(system, relu)
            milp = gurobi_torch_mip.GurobiTorchMILP(self.dtype)
            x = milp.addVars(system.x_dim,
                             lb=-gurobipy.GRB.INFINITY,
                             vtype=gurobipy.GRB.CONTINUOUS)
            (relu_z, _, a_relu,
             b_relu) = dut.add_lyap_relu_output_constraint(milp, x)
            (s, _) = dut.add_state_error_l1_constraint(milp,
                                                       x_equilibrium,
                                                       x,
                                                       R=R)
            relu_xhat_coeff, relu_xhat_var, relu_xhat_constant, _ = \
                dut._add_relu_xhat_constraint(
                    milp, x, x_equilibrium, xhat_indices)
            dut.add_lyapunov_bounds_constraint(lyapunov_lower, lyapunov_upper,
                                               milp, a_relu, b_relu, V_lambda,
                                               relu_z, relu_xhat_coeff,
                                               relu_xhat_var,
                                               relu_xhat_constant, s)
            for i in range(system.x_dim):
                milp.addLConstr([torch.tensor([1.], dtype=system.dtype)],
                                [[x[i]]],
                                sense=gurobipy.GRB.EQUAL,
                                rhs=x_val[i])
            milp.gurobi_model.setParam(gurobipy.GRB.Param.OutputFlag, 0)
            milp.gurobi_model.setParam(gurobipy.GRB.Param.DualReductions, 0)
            milp.gurobi_model.optimize()

            V_val = dut.lyapunov_value(x_val, x_equilibrium, V_lambda, R=R)
            is_satisfied = True
            if lyapunov_lower is not None:
                is_satisfied = is_satisfied and V_val >= lyapunov_lower
            if lyapunov_upper is not None:
                is_satisfied = is_satisfied and V_val <= lyapunov_upper
            if is_satisfied:
                self.assertEqual(milp.gurobi_model.status,
                                 gurobipy.GRB.Status.OPTIMAL)
            else:
                self.assertEqual(milp.gurobi_model.status,
                                 gurobipy.GRB.Status.INFEASIBLE)

        lyapunov_relu1 = setup_relu(self.dtype)
        lyapunov_relu2 = setup_leaky_relu(self.dtype)
        R = torch.tensor([[1, 1], [-1, 1], [0, 1]], dtype=self.dtype)
        for relu in (lyapunov_relu1, lyapunov_relu2):
            test_fun(None,
                     None,
                     self.system1,
                     relu,
                     self.x_equilibrium1,
                     R,
                     torch.tensor([-0.5, 0.3], dtype=self.dtype),
                     xhat_indices=None)
            test_fun(0.5,
                     None,
                     self.system1,
                     relu,
                     self.x_equilibrium1,
                     R,
                     torch.tensor([-0.5, 0.3], dtype=self.dtype),
                     xhat_indices=None)
            test_fun(None,
                     30.,
                     self.system1,
                     relu,
                     self.x_equilibrium1,
                     R,
                     torch.tensor([-0.5, 0.3], dtype=self.dtype),
                     xhat_indices=None)
            test_fun(-2.,
                     30.,
                     self.system1,
                     relu,
                     self.x_equilibrium1,
                     R,
                     torch.tensor([-0.5, 0.3], dtype=self.dtype),
                     xhat_indices=None)
            test_fun(-2.,
                     1.,
                     self.system1,
                     relu,
                     self.x_equilibrium1,
                     R,
                     torch.tensor([-0.1, 0.4], dtype=self.dtype),
                     xhat_indices=None)
            test_fun(1.,
                     3.,
                     self.system1,
                     relu,
                     self.x_equilibrium1,
                     R,
                     torch.tensor([0.3, 0.4], dtype=self.dtype),
                     xhat_indices=None)
            test_fun(1.,
                     3.,
                     self.system1,
                     relu,
                     self.x_equilibrium1,
                     R,
                     torch.tensor([0.3, 0.4], dtype=self.dtype),
                     xhat_indices=[0])

    def lyapunov_value_tester(self, system, lyap_relu, x_equilibrium, V_lambda,
                              R, xbar_indices, xhat_indices, x_val):
        dut = lyapunov.LyapunovHybridLinearSystem(system, lyap_relu)
        lyap_val = dut.lyapunov_value(x_val,
                                      x_equilibrium,
                                      V_lambda,
                                      R=R,
                                      xbar_indices=xbar_indices,
                                      xhat_indices=xhat_indices)

        def eval_lyap(x):
            xbar_indices_tmp = compute_xhat._get_xbar_indices(
                dut.system.x_dim, xbar_indices)
            xhat = compute_xhat._get_xhat_val(x, x_equilibrium, xhat_indices)
            return lyap_relu(x) - lyap_relu(xhat) + V_lambda * torch.norm(
                R @ (x[xbar_indices_tmp] - x_equilibrium[xbar_indices_tmp]),
                p=1)

        if len(x_val.shape) == 1:
            self.assertAlmostEqual(eval_lyap(x_val).item(), lyap_val.item())
        else:
            self.assertEqual(x_val.shape[0], lyap_val.shape[0])
            for i in range(lyap_val.shape[0]):
                self.assertAlmostEqual(
                    eval_lyap(x_val[i]).item(), lyap_val[i].item())

    def test_lyapunov_value(self):
        relu = setup_leaky_relu(self.system1.dtype)
        V_lambda = 0.1
        R = torch.tensor([[1, 1], [-1, 1], [0, 1]], dtype=self.dtype)

        for x_equilibrium in (self.x_equilibrium1, self.x_equilibrium2):
            self.lyapunov_value_tester(self.system1,
                                       relu,
                                       x_equilibrium,
                                       V_lambda,
                                       R,
                                       xbar_indices=None,
                                       xhat_indices=None,
                                       x_val=torch.tensor([0, 0],
                                                          dtype=self.dtype))
            self.lyapunov_value_tester(self.system1,
                                       relu,
                                       x_equilibrium,
                                       V_lambda,
                                       R,
                                       xbar_indices=None,
                                       xhat_indices=None,
                                       x_val=torch.tensor([1, 0],
                                                          dtype=self.dtype))
            self.lyapunov_value_tester(self.system1,
                                       relu,
                                       x_equilibrium,
                                       V_lambda,
                                       R,
                                       xbar_indices=None,
                                       xhat_indices=None,
                                       x_val=torch.tensor([-0.2, 0.4],
                                                          dtype=self.dtype))

        for system, x_equilibrium in \
                ((self.system1, self.x_equilibrium1),
                 (self.system2, self.x_equilibrium2)):
            x_val = torch.tensor(
                [[0., 0.], [1., 0.], [0., 1.], [0.2, 0.4], [0.5, -0.8]],
                dtype=self.dtype)
            self.lyapunov_value_tester(system,
                                       relu,
                                       x_equilibrium,
                                       V_lambda,
                                       R,
                                       xbar_indices=None,
                                       xhat_indices=None,
                                       x_val=x_val)

    def test_lyapunov_value_partial_state(self):
        V_lambda = 0.5
        R = torch.tensor([[1, 0.5], [0.3, -0.5], [0.2, -1.4]],
                         dtype=self.dtype)
        torch.manual_seed(0)
        lyap_relu = utils.setup_relu((3, 5, 1),
                                     params=None,
                                     negative_slope=0.1,
                                     bias=True,
                                     dtype=self.dtype)
        system = setup_relu_dyn_3d(self.dtype)
        self.lyapunov_value_tester(system,
                                   lyap_relu,
                                   system.x_equilibrium,
                                   V_lambda,
                                   R,
                                   xbar_indices=[0, 2],
                                   xhat_indices=None,
                                   x_val=torch.tensor([0.5, 0.2, 1.5],
                                                      dtype=self.dtype))
        self.lyapunov_value_tester(system,
                                   lyap_relu,
                                   system.x_equilibrium,
                                   V_lambda,
                                   R,
                                   xbar_indices=[0, 2],
                                   xhat_indices=[1, 2],
                                   x_val=torch.tensor([0.5, 0.2, 1.5],
                                                      dtype=self.dtype))
        # Test a batch of states.
        self.lyapunov_value_tester(system,
                                   lyap_relu,
                                   system.x_equilibrium,
                                   V_lambda,
                                   R,
                                   xbar_indices=[0, 2],
                                   xhat_indices=None,
                                   x_val=torch.tensor(
                                       [[0.5, 0.2, 1.5], [0.2, 1.5, -0.3]],
                                       dtype=self.dtype))
        self.lyapunov_value_tester(system,
                                   lyap_relu,
                                   system.x_equilibrium,
                                   V_lambda,
                                   R,
                                   xbar_indices=[0, 2],
                                   xhat_indices=[1, 2],
                                   x_val=torch.tensor(
                                       [[0.5, 0.2, 1.5], [0.2, 1.5, -0.3]],
                                       dtype=self.dtype))

    def test_lyapunov_positivity_loss_at_samples(self):
        # Construct a simple ReLU model with 2 hidden layers
        linear1 = nn.Linear(2, 3)
        linear1.weight.data = torch.tensor([[1, 2], [3, 4], [5, 6]],
                                           dtype=self.dtype)
        linear1.bias.data = torch.tensor([-11, 10, 5], dtype=self.dtype)
        linear2 = nn.Linear(3, 4)
        linear2.weight.data = torch.tensor(
            [[-1, -0.5, 1.5], [2, 5, 6], [-2, -3, -4], [1.5, 4, 6]],
            dtype=self.dtype)
        linear2.bias.data = torch.tensor([-3, 2, 0.7, 1.5], dtype=self.dtype)
        linear3 = nn.Linear(4, 1)
        linear3.weight.data = torch.tensor([[4, 5, 6, 7]], dtype=self.dtype)
        linear3.bias.data = torch.tensor([-9], dtype=self.dtype)
        lyapunov_relu1 = nn.Sequential(linear1, nn.ReLU(), linear2, nn.ReLU(),
                                       linear3)
        dut = lyapunov.LyapunovDiscreteTimeHybridSystem(
            self.system1, lyapunov_relu1)

        x_equilibrium = torch.tensor([0., 0.], dtype=self.dtype)
        relu_at_equilibrium = lyapunov_relu1.forward(x_equilibrium)

        V_lambda = 0.01
        margin = 2.
        epsilon = 2.
        R = torch.tensor([[1, 1], [-1, 1], [0, 1]], dtype=self.dtype)

        def test_fun(x_samples):
            losses = torch.zeros((x_samples.shape[0], ), dtype=x_samples.dtype)
            for i in range(x_samples.shape[0]):
                relu_x = lyapunov_relu1.forward(x_samples[i])
                v = (relu_x - relu_at_equilibrium) + V_lambda * torch.norm(
                    R @ (x_samples[i] - x_equilibrium), p=1)
                v_minus_l1 = v - epsilon * torch.norm(
                    R @ (x_samples[i] - x_equilibrium), p=1)
                losses[i] = 0 if v_minus_l1 > margin else margin - v_minus_l1
            self.assertAlmostEqual(
                torch.mean(losses).item(),
                dut.lyapunov_positivity_loss_at_samples(x_equilibrium,
                                                        x_samples,
                                                        V_lambda,
                                                        epsilon,
                                                        R=R,
                                                        margin=margin).item())
            self.assertAlmostEqual(
                torch.max(losses).item(),
                dut.lyapunov_positivity_loss_at_samples(
                    x_equilibrium,
                    x_samples,
                    V_lambda,
                    epsilon,
                    R=R,
                    margin=margin,
                    reduction="max").item())

            self.assertAlmostEqual(
                torch.norm(losses, p=4).item(),
                dut.lyapunov_positivity_loss_at_samples(
                    x_equilibrium,
                    x_samples,
                    V_lambda,
                    epsilon,
                    R=R,
                    margin=margin,
                    reduction="4norm").item())

        test_fun(torch.tensor([[0, 0]], dtype=self.dtype))
        test_fun(torch.tensor([[0, 0], [0, 1]], dtype=self.dtype))
        test_fun(
            torch.tensor([[0, 0], [0, 1], [1, 0], [0.5, 0.4], [0.2, -0.1],
                          [0.4, 0.3], [-0.2, 0.3]],
                         dtype=self.dtype))

    def test_lyapunov_positivity_loss_at_samples_partial_state(self):
        torch.manual_seed(0)
        system = setup_relu_dyn_3d(self.dtype)
        lyap_relu = utils.setup_relu((3, 5, 1),
                                     params=None,
                                     negative_slope=0.1,
                                     bias=True,
                                     dtype=self.dtype)
        dut = lyapunov.LyapunovDiscreteTimeHybridSystem(system, lyap_relu)

        V_lambda = 0.5
        state_samples = torch.tensor(
            [[0.2, 0.4, 0.1], [-2, -0.4, 1.2], [0.3, 0.9, 0.5]],
            dtype=self.dtype)
        V_epsilon = 0.4
        R = torch.tensor([[0.4, -.2], [0.5, -1.2], [0.3, -1]],
                         dtype=self.dtype)
        margin = 0.001

        xbar_indices = [0, 2]
        xhat_indices = [1, 2]
        loss = dut.lyapunov_positivity_loss_at_samples(
            system.x_equilibrium,
            state_samples,
            V_lambda,
            V_epsilon,
            R=R,
            margin=margin,
            xbar_indices=xbar_indices,
            xhat_indices=xhat_indices)
        loss_expected = 0
        for i in range(state_samples.shape[0]):
            loss_expected += np.max(
                ((-dut.lyapunov_value(state_samples[i],
                                      system.x_equilibrium,
                                      V_lambda,
                                      R=R,
                                      xbar_indices=xbar_indices,
                                      xhat_indices=xhat_indices) + V_epsilon *
                  torch.norm(R @ (state_samples[i, xbar_indices] -
                                  system.x_equilibrium[xbar_indices]),
                             p=1) + margin).item(), 0))
        self.assertAlmostEqual(loss.item(),
                               loss_expected / state_samples.shape[0])


class TestLyapunovDiscreteTimeHybridSystem(unittest.TestCase):
    def setUp(self):
        """
        The piecewise affine system is from "Analysis of discrete-time
        piecewise affine and hybrid systems" by Giancarlo Ferrari-Trecate
        et.al.
        """
        self.dtype = torch.float64
        self.x_equilibrium1 = torch.tensor([0, 0], dtype=self.dtype)
        self.system1 = test_hybrid_linear_system.\
            setup_trecate_discrete_time_system()
        self.theta2 = np.pi / 5
        cos_theta2 = np.cos(self.theta2)
        sin_theta2 = np.sin(self.theta2)
        self.R2 = torch.tensor(
            [[cos_theta2, -sin_theta2], [sin_theta2, cos_theta2]],
            dtype=self.dtype)
        self.x_equilibrium2 = torch.tensor([0.4, 2.5], dtype=self.dtype)
        self.system2 = \
            test_hybrid_linear_system.setup_transformed_trecate_system(
                self.theta2, self.x_equilibrium2)
        relu_dyn = setup_relu_dyn(self.dtype)
        x_lo = torch.tensor([-3, -3], dtype=self.dtype)
        x_up = torch.tensor([3, 3], dtype=self.dtype)
        self.system3 = relu_system.AutonomousReLUSystem(
            self.dtype, x_lo, x_up, relu_dyn)
        self.x_equilibrium3 = torch.tensor([0., 0.], dtype=self.dtype)

        self.system4 = setup_hybrid_feedback_system(self.dtype)
        self.x_equilibrium4 = self.system4.x_equilibrium

    def lyapunov_derivative_tester(self, system, lyap_relu, x, x_equilibrium,
                                   V_lambda, epsilon, R, xbar_indices,
                                   xhat_indices):
        x_next_possible = system.possible_dx(x)
        dut = lyapunov.LyapunovDiscreteTimeHybridSystem(system, lyap_relu)
        V_next_possible = [
            dut.lyapunov_value(x_next,
                               x_equilibrium,
                               V_lambda,
                               R=R,
                               xbar_indices=xbar_indices,
                               xhat_indices=xhat_indices)
            for x_next in x_next_possible
        ]
        V = dut.lyapunov_value(x,
                               x_equilibrium,
                               V_lambda,
                               R=R,
                               xbar_indices=xbar_indices,
                               xhat_indices=xhat_indices)
        lyapunov_derivative_expected = [
            V_next - V + epsilon * V for V_next in V_next_possible
        ]
        lyapunov_derivative = dut.lyapunov_derivative(
            x,
            x_equilibrium,
            V_lambda,
            epsilon,
            R=R,
            xbar_indices=xbar_indices,
            xhat_indices=xhat_indices)
        self.assertEqual(len(lyapunov_derivative),
                         len(lyapunov_derivative_expected))
        for i in range(len(lyapunov_derivative)):
            self.assertAlmostEqual(lyapunov_derivative[i].item(),
                                   lyapunov_derivative_expected[i].item())

    def test_lyapunov_derivative(self):
        relu = setup_relu(torch.float64)
        V_lambda = 2.
        epsilon = 0.1
        R = torch.tensor([[1, 1], [-1, 1], [0, 1]], dtype=self.dtype)

        for x in ([0.2, 0.5], [0.1, -0.4], [0., 0.5], [-0.2, 0.]):
            self.lyapunov_derivative_tester(self.system1,
                                            relu,
                                            torch.tensor(
                                                x, dtype=self.system1.dtype),
                                            self.x_equilibrium1,
                                            V_lambda,
                                            epsilon,
                                            R,
                                            xbar_indices=None,
                                            xhat_indices=None)
            self.lyapunov_derivative_tester(
                self.system2,
                relu,
                self.R2 @ torch.tensor(x, dtype=self.system2.dtype) +
                self.x_equilibrium2,
                self.x_equilibrium2,
                V_lambda,
                epsilon,
                R,
                xbar_indices=None,
                xhat_indices=None)
            self.lyapunov_derivative_tester(self.system3,
                                            relu,
                                            torch.tensor(
                                                x, dtype=self.system3.dtype),
                                            self.x_equilibrium3,
                                            V_lambda,
                                            epsilon,
                                            R,
                                            xbar_indices=None,
                                            xhat_indices=None)
            self.lyapunov_derivative_tester(self.system4,
                                            relu,
                                            torch.tensor(
                                                x, dtype=self.system4.dtype),
                                            self.x_equilibrium4,
                                            V_lambda,
                                            epsilon,
                                            R,
                                            xbar_indices=None,
                                            xhat_indices=None)

    def test_lyapunov_derivative_partial_state(self):
        torch.manual_seed(0)
        lyap_relu = utils.setup_relu((3, 5, 1),
                                     params=None,
                                     negative_slope=0.1,
                                     bias=True,
                                     dtype=self.dtype)
        system = setup_relu_dyn_3d(self.dtype)
        V_lambda = 0.5
        epsilon = 0.3
        R = torch.tensor([[0.1, 0.4], [0.2, -0.5], [0.3, 0.9]],
                         dtype=self.dtype)
        xbar_indices = [0, 2]
        xhat_indices = [0, 1]
        self.lyapunov_derivative_tester(
            system, lyap_relu, torch.tensor([0.2, 0.9, 0.4], dtype=self.dtype),
            system.x_equilibrium, V_lambda, epsilon, R, xbar_indices,
            xhat_indices)

    def lyapunov_positivity_as_milp_tester(self, system, relu, x_equilibrium,
                                           V_lambda, V_epsilon, R,
                                           xbar_indices, xhat_indices, x_val):
        # Fix x to x_val. Now check if the optimal cost is
        # (ε-λ) * |R*(x̅ - x̅*)|₁ - ReLU(x) + ReLU(x̂)
        dut = lyapunov.LyapunovDiscreteTimeHybridSystem(system, relu)
        (milp, x) = dut.lyapunov_positivity_as_milp(x_equilibrium,
                                                    V_lambda,
                                                    V_epsilon,
                                                    R=R,
                                                    xbar_indices=xbar_indices,
                                                    xhat_indices=xhat_indices,
                                                    fixed_R=True)
        for i in range(dut.system.x_dim):
            milp.addLConstr([torch.tensor([1.], dtype=self.dtype)], [[x[i]]],
                            rhs=x_val[i],
                            sense=gurobipy.GRB.EQUAL)
        milp.gurobi_model.setParam(gurobipy.GRB.Param.OutputFlag, False)
        milp.gurobi_model.optimize()
        self.assertEqual(milp.gurobi_model.status, gurobipy.GRB.Status.OPTIMAL)
        xbar_indices = compute_xhat._get_xbar_indices(dut.system.x_dim,
                                                      xbar_indices)
        xhat_val = compute_xhat._get_xhat_val(x_val, x_equilibrium,
                                              xhat_indices)
        self.assertAlmostEqual(
            milp.gurobi_model.ObjVal, -relu(x_val).item() +
            relu(xhat_val).item() + (V_epsilon - V_lambda) *
            torch.norm(R @ (x_val[xbar_indices] - x_equilibrium[xbar_indices]),
                       p=1).item())

    def test_lyapunov_positivity_as_milp(self):
        lyapunov_relu1 = setup_relu(self.dtype)
        lyapunov_relu2 = setup_leaky_relu(self.dtype, bias=False)
        V_epsilon = 0.01
        V_lambda = 0.1
        R = torch.tensor([[1, 1], [-1, 1], [0, 1]], dtype=self.dtype)

        for system in [self.system1, self.system3]:
            self.lyapunov_positivity_as_milp_tester(system,
                                                    lyapunov_relu1,
                                                    self.x_equilibrium1,
                                                    V_lambda,
                                                    V_epsilon,
                                                    R,
                                                    xbar_indices=None,
                                                    xhat_indices=None,
                                                    x_val=torch.tensor(
                                                        [0, 0],
                                                        dtype=self.dtype))
            self.lyapunov_positivity_as_milp_tester(system,
                                                    lyapunov_relu1,
                                                    self.x_equilibrium1,
                                                    V_lambda,
                                                    V_epsilon,
                                                    R,
                                                    xbar_indices=None,
                                                    xhat_indices=None,
                                                    x_val=torch.tensor(
                                                        [0, 0.5],
                                                        dtype=self.dtype))
            self.lyapunov_positivity_as_milp_tester(system,
                                                    lyapunov_relu1,
                                                    self.x_equilibrium1,
                                                    V_lambda,
                                                    V_epsilon,
                                                    R,
                                                    xbar_indices=None,
                                                    xhat_indices=None,
                                                    x_val=torch.tensor(
                                                        [0.1, 0.5],
                                                        dtype=self.dtype))
            self.lyapunov_positivity_as_milp_tester(system,
                                                    lyapunov_relu1,
                                                    self.x_equilibrium1,
                                                    V_lambda,
                                                    V_epsilon,
                                                    R,
                                                    xbar_indices=None,
                                                    xhat_indices=None,
                                                    x_val=torch.tensor(
                                                        [-0.3, 0.8],
                                                        dtype=self.dtype))
            self.lyapunov_positivity_as_milp_tester(system,
                                                    lyapunov_relu1,
                                                    self.x_equilibrium1,
                                                    V_lambda,
                                                    V_epsilon,
                                                    R,
                                                    xbar_indices=None,
                                                    xhat_indices=None,
                                                    x_val=torch.tensor(
                                                        [-0.3, -0.2],
                                                        dtype=self.dtype))
            self.lyapunov_positivity_as_milp_tester(system,
                                                    lyapunov_relu1,
                                                    self.x_equilibrium1,
                                                    V_lambda,
                                                    V_epsilon,
                                                    R,
                                                    xbar_indices=None,
                                                    xhat_indices=None,
                                                    x_val=torch.tensor(
                                                        [0.6, -0.2],
                                                        dtype=self.dtype))
        self.lyapunov_positivity_as_milp_tester(self.system2,
                                                lyapunov_relu1,
                                                self.x_equilibrium2,
                                                V_lambda,
                                                V_epsilon,
                                                R,
                                                xbar_indices=None,
                                                xhat_indices=None,
                                                x_val=self.x_equilibrium2)
        self.lyapunov_positivity_as_milp_tester(
            self.system2,
            lyapunov_relu1,
            self.x_equilibrium2,
            V_lambda,
            V_epsilon,
            R,
            xbar_indices=None,
            xhat_indices=None,
            x_val=self.R2 @ torch.tensor([0, 0.5], dtype=self.dtype) +
            self.x_equilibrium2)
        self.lyapunov_positivity_as_milp_tester(
            self.system2,
            lyapunov_relu1,
            self.x_equilibrium2,
            V_lambda,
            V_epsilon,
            R,
            xbar_indices=None,
            xhat_indices=None,
            x_val=self.R2 @ torch.tensor([0.1, 0.5], dtype=self.dtype) +
            self.x_equilibrium2)
        self.lyapunov_positivity_as_milp_tester(
            self.system2,
            lyapunov_relu1,
            self.x_equilibrium2,
            V_lambda,
            V_epsilon,
            R,
            xbar_indices=None,
            xhat_indices=None,
            x_val=self.R2 @ torch.tensor([-0.3, 0.5], dtype=self.dtype) +
            self.x_equilibrium2)
        self.lyapunov_positivity_as_milp_tester(
            self.system2,
            lyapunov_relu1,
            self.x_equilibrium2,
            V_lambda,
            V_epsilon,
            R,
            xbar_indices=None,
            xhat_indices=None,
            x_val=self.R2 @ torch.tensor([0.5, -0.8], dtype=self.dtype) +
            self.x_equilibrium2)
        self.lyapunov_positivity_as_milp_tester(
            self.system2,
            lyapunov_relu1,
            self.x_equilibrium2,
            V_lambda,
            V_epsilon,
            R,
            xbar_indices=None,
            xhat_indices=None,
            x_val=self.R2 @ torch.tensor([-0.2, -0.8], dtype=self.dtype) +
            self.x_equilibrium2)
        self.lyapunov_positivity_as_milp_tester(self.system1,
                                                lyapunov_relu2,
                                                self.x_equilibrium1,
                                                V_lambda,
                                                V_epsilon,
                                                R,
                                                xbar_indices=None,
                                                xhat_indices=None,
                                                x_val=torch.tensor(
                                                    [-0.3, -0.2],
                                                    dtype=self.dtype))
        self.lyapunov_positivity_as_milp_tester(self.system1,
                                                lyapunov_relu2,
                                                self.x_equilibrium1,
                                                V_lambda,
                                                V_epsilon,
                                                R,
                                                xbar_indices=None,
                                                xhat_indices=None,
                                                x_val=torch.tensor(
                                                    [0.5, -0.2],
                                                    dtype=self.dtype))

    def test_lyapunov_positivity_as_milp_partial_state(self):
        # test lyapunov_positivity_as_milp with xbar_indices not equal to None.
        torch.manual_seed(0)
        system = setup_relu_dyn_3d(self.dtype)
        lyap_relu = utils.setup_relu((3, 5, 1),
                                     params=None,
                                     bias=True,
                                     negative_slope=0.1,
                                     dtype=self.dtype)
        V_lambda = 0.5
        V_epsilon = 0.4
        R = torch.tensor([[0.2, 0.1], [1., 0.5], [0.3, 0.2]], dtype=self.dtype)
        self.lyapunov_positivity_as_milp_tester(system,
                                                lyap_relu,
                                                system.x_equilibrium,
                                                V_lambda,
                                                V_epsilon,
                                                R,
                                                xbar_indices=[0, 2],
                                                xhat_indices=[0, 1],
                                                x_val=torch.tensor(
                                                    [0.1, 0.4, -0.5],
                                                    dtype=self.dtype))
        self.lyapunov_positivity_as_milp_tester(system,
                                                lyap_relu,
                                                system.x_equilibrium,
                                                V_lambda,
                                                V_epsilon,
                                                R,
                                                xbar_indices=[1, 2],
                                                xhat_indices=[0, 1],
                                                x_val=torch.tensor(
                                                    [0.1, 0.4, -0.5],
                                                    dtype=self.dtype))

    def lyapunov_derivative_as_milp_check_state(self, system, x_equilibrium,
                                                relu, V_lambda, dV_epsilon,
                                                eps_type, R, fixed_R,
                                                xbar_indices, xhat_indices):
        # Test if the MILP solution satisfies x_next = f(x) and the objective
        # value is v_next - v + dv_epsilon * v.
        dut = lyapunov.LyapunovDiscreteTimeHybridSystem(system, relu)
        (milp, x, beta, gamma, x_next, s, z, z_next, beta_next) =\
            dut.lyapunov_derivative_as_milp(
                x_equilibrium, V_lambda, dV_epsilon, eps_type, R=R,
                fixed_R=fixed_R, xbar_indices=xbar_indices,
                xhat_indices=xhat_indices)
        # First solve this MILP. The solution has to satisfy that
        # x_next = Ai * x + g_i where i is the active mode inferred from
        # gamma.
        milp.gurobi_model.setParam(gurobipy.GRB.Param.OutputFlag, 0)
        milp.gurobi_model.optimize()
        if (milp.gurobi_model.status == gurobipy.GRB.Status.INFEASIBLE):
            milp.gurobi_model.computeIIS()
            milp.gurobi_model.write("milp.ilp")
        self.assertEqual(milp.gurobi_model.status, gurobipy.GRB.Status.OPTIMAL)
        x_sol = np.array([var.x for var in x])
        x_next_sol = np.array([var.x for var in x_next])
        if isinstance(system,
                      hybrid_linear_system.AutonomousHybridLinearSystem):
            gamma_sol = np.array([var.x for var in gamma])
            self.assertAlmostEqual(np.sum(gamma_sol), 1)
            for mode in range(self.system1.num_modes):
                if np.abs(gamma_sol[mode] - 1) < 1E-4:
                    # This mode is active.
                    # Pi * x <= qi
                    np.testing.assert_array_less(
                        dut.system.P[mode].detach().numpy() @ x_sol,
                        dut.system.q[mode].detach().numpy() + 1E-5)
                    # x_next = Ai * x + gi
                    np.testing.assert_array_almost_equal(
                        dut.system.A[mode].detach().numpy() @ x_sol +
                        dut.system.g[mode].detach().numpy(),
                        x_next_sol,
                        decimal=5)
        else:
            np.testing.assert_array_almost_equal(system.step_forward(
                torch.tensor(x_sol, dtype=self.dtype)).detach().numpy(),
                                                 x_next_sol,
                                                 decimal=5)
        v_next = dut.lyapunov_value(torch.from_numpy(x_next_sol),
                                    x_equilibrium,
                                    V_lambda,
                                    R=R,
                                    xbar_indices=xbar_indices,
                                    xhat_indices=xhat_indices)
        v = dut.lyapunov_value(torch.from_numpy(x_sol),
                               x_equilibrium,
                               V_lambda,
                               R=R,
                               xbar_indices=xbar_indices,
                               xhat_indices=xhat_indices)
        if eps_type == lyapunov.ConvergenceEps.ExpLower:
            objVal_expected = (v_next - v + dV_epsilon * v).item()
        elif eps_type == lyapunov.ConvergenceEps.ExpUpper:
            objVal_expected = -(v_next - v + dV_epsilon * v).item()
        elif eps_type == lyapunov.ConvergenceEps.Asymp:
            xbar_indices = compute_xhat._get_xbar_indices(
                dut.system.x_dim, xbar_indices)
            objVal_expected = (
                v_next - v + dV_epsilon * torch.norm(R @ (torch.from_numpy(
                    x_sol[xbar_indices]) - x_equilibrium[xbar_indices]),
                                                     p=1)).item()
        self.assertAlmostEqual(milp.gurobi_model.objVal, objVal_expected)

    def compute_optimal_cost_lyapunov_derivative_as_milp(
            self, system, relu, x_equilibrium, V_lambda, dV_epsilon, eps_type,
            R, fixed_R, xbar_indices, xhat_indices):
        # Now solve lyapunov derivative MILP to optimal, return the optimal
        # cost
        dut = lyapunov.LyapunovDiscreteTimeHybridSystem(system, relu)
        milp = dut.lyapunov_derivative_as_milp(x_equilibrium,
                                               V_lambda,
                                               dV_epsilon,
                                               eps_type,
                                               R=R,
                                               fixed_R=fixed_R,
                                               xbar_indices=xbar_indices,
                                               xhat_indices=xhat_indices)[0]
        milp.gurobi_model.setParam(gurobipy.GRB.Param.OutputFlag, 0)
        milp.gurobi_model.optimize()
        milp_optimal_cost = milp.gurobi_model.ObjVal
        return milp_optimal_cost

    def lyapunov_derivative_as_milp_fixed_state(
            self, system, relu, x_equilibrium, V_lambda, dV_epsilon, eps_type,
            R, fixed_R, x_val, lyapunov_derivative_milp_optimal_cost,
            xbar_indices, xhat_indices):
        # Now solve MILP to optimal
        # We will sample many states later, and evaluate dV + epsilon*V at
        # each state, the sampled value should all be less than the optimal
        # value.
        # Now test reformulating the Lyapunov derivative loss as a
        # mixed-integer linear program. We fix x[n] to some value, compute the
        # cost function of the MILP, and then check if it is the same as
        # evaluating the ReLU network on x[n] and x[n+1]
        dut = lyapunov.LyapunovDiscreteTimeHybridSystem(system, relu)
        if isinstance(dut.system,
                      hybrid_linear_system.AutonomousHybridLinearSystem):
            x_next_val = dut.system.step_forward(x_val)
        elif isinstance(dut.system, relu_system.AutonomousReLUSystem):
            x_next_val = dut.system.step_forward(x_val)
        else:
            raise (NotImplementedError)
        v_next = dut.lyapunov_value(x_next_val,
                                    x_equilibrium,
                                    V_lambda,
                                    R=R,
                                    xbar_indices=xbar_indices,
                                    xhat_indices=xhat_indices)
        v = dut.lyapunov_value(x_val,
                               x_equilibrium,
                               V_lambda,
                               R=R,
                               xbar_indices=xbar_indices,
                               xhat_indices=xhat_indices)
        if eps_type == lyapunov.ConvergenceEps.ExpLower:
            cost_expected = (v_next - v + dV_epsilon * v).item()
        elif eps_type == lyapunov.ConvergenceEps.ExpUpper:
            cost_expected = -(v_next - v + dV_epsilon * v).item()
        elif eps_type == lyapunov.ConvergenceEps.Asymp:
            xbar_indices = compute_xhat._get_xbar_indices(
                dut.system.x_dim, xbar_indices)
            cost_expected = (v_next - v + dV_epsilon * torch.norm(
                R @ (x_val[xbar_indices] - x_equilibrium[xbar_indices]), p=1)
                             ).item()
        (milp_test, x_test, _, _, _, _, _, _, _) =\
            dut.lyapunov_derivative_as_milp(
                x_equilibrium, V_lambda, dV_epsilon, eps_type, R=R,
                fixed_R=fixed_R, xbar_indices=xbar_indices,
                xhat_indices=xhat_indices)
        for i in range(dut.system.x_dim):
            milp_test.addLConstr([torch.tensor([1.], dtype=milp_test.dtype)],
                                 [[x_test[i]]],
                                 rhs=x_val[i],
                                 sense=gurobipy.GRB.EQUAL)
        milp_test.gurobi_model.setParam(gurobipy.GRB.Param.OutputFlag, 0)
        milp_test.gurobi_model.optimize()
        self.assertEqual(milp_test.gurobi_model.status,
                         gurobipy.GRB.Status.OPTIMAL)
        self.assertAlmostEqual(cost_expected, milp_test.gurobi_model.objVal)
        # milp_test solves the problem with fixed x[n], so it should
        # achieve less optimal cost than milp
        self.assertLessEqual(milp_test.gurobi_model.objVal,
                             lyapunov_derivative_milp_optimal_cost)

    def sample_state(self, system, mode=None):
        if isinstance(system,
                      hybrid_linear_system.AutonomousHybridLinearSystem):
            while True:
                x_val = torch.tensor([
                    np.random.uniform(system.x_lo_all[i], system.x_up_all[i])
                    for i in range(system.x_dim)
                ]).type(system.dtype)
                if torch.all(system.P[mode] @ x_val <= system.q[mode]):
                    x_val_next = system.step_forward(x_val, mode)
                    if (torch.all(
                            x_val_next <= torch.from_numpy(system.x_up_all))
                            and torch.all(x_val_next >= torch.from_numpy(
                                system.x_lo_all))):
                        return x_val
        elif isinstance(system, relu_system.AutonomousReLUSystem):
            while True:
                x_val = torch.rand(system.x_dim, dtype=system.dtype) * (
                    system.x_up - system.x_lo) + system.x_lo
                x_val_next = system.step_forward(x_val)
                if (torch.all(x_val_next <= system.x_up)
                        and torch.all(x_val_next >= system.x_lo)):
                    return x_val
        else:
            raise (NotImplementedError)

    def lyapunov_derivative_as_milp_tester(self, system, x_equilibrium,
                                           x_samples, eps_type, R,
                                           xbar_indices, xhat_indices):
        lyapunov_relu1 = setup_leaky_relu(self.dtype)
        lyapunov_relu2 = setup_relu(self.dtype)
        V_lambda = 2.
        dV_epsilon = 0.1
        self.lyapunov_derivative_as_milp_check_state(system,
                                                     x_equilibrium,
                                                     lyapunov_relu1,
                                                     V_lambda,
                                                     dV_epsilon,
                                                     eps_type,
                                                     R,
                                                     fixed_R=True,
                                                     xbar_indices=xbar_indices,
                                                     xhat_indices=xhat_indices)
        self.lyapunov_derivative_as_milp_check_state(system,
                                                     x_equilibrium,
                                                     lyapunov_relu2,
                                                     V_lambda,
                                                     dV_epsilon,
                                                     eps_type,
                                                     R,
                                                     fixed_R=True,
                                                     xbar_indices=xbar_indices,
                                                     xhat_indices=xhat_indices)

        lyapunov_derivative_milp_optimal_cost = \
            self.compute_optimal_cost_lyapunov_derivative_as_milp(
                system, lyapunov_relu1, x_equilibrium, V_lambda, dV_epsilon,
                eps_type, R, fixed_R=True, xbar_indices=xbar_indices,
                xhat_indices=xhat_indices)
        for x_val in x_samples:
            self.lyapunov_derivative_as_milp_fixed_state(
                system,
                lyapunov_relu1,
                x_equilibrium,
                V_lambda,
                dV_epsilon,
                eps_type,
                R,
                fixed_R=True,
                x_val=x_val,
                # yapf: disable
                lyapunov_derivative_milp_optimal_cost=lyapunov_derivative_milp_optimal_cost,  # noqa
                xbar_indices=xbar_indices,
                xhat_indices=xhat_indices)
            # yapf: enable

    def test_lyapunov_derivative_as_milp1(self):
        # Test for system 1
        torch.manual_seed(0)
        np.random.seed(0)
        x_samples = []
        R = torch.tensor([[1., 1], [-1., 1], [0, 1]], dtype=self.dtype)
        xbar_indices = None
        xhat_indices = None
        for mode in range(self.system1.num_modes):
            for _ in range(20):
                x_samples.append(self.sample_state(self.system1, mode))

        for eps_type in list(lyapunov.ConvergenceEps):
            self.lyapunov_derivative_as_milp_tester(self.system1,
                                                    self.x_equilibrium1,
                                                    x_samples, eps_type, R,
                                                    xbar_indices, xhat_indices)

    def test_lyapunov_derivative_as_milp2(self):
        # Test for system2
        torch.manual_seed(0)
        np.random.seed(0)
        x_samples = []
        R = torch.tensor([[1., 1], [-1., 1], [0, 1]], dtype=self.dtype)
        xbar_indices = None
        xhat_indices = None
        for mode in range(self.system2.num_modes):
            for _ in range(20):
                x_samples.append(self.sample_state(self.system2, mode))

        for eps_type in list(lyapunov.ConvergenceEps):
            self.lyapunov_derivative_as_milp_tester(self.system2,
                                                    self.x_equilibrium2,
                                                    x_samples, eps_type, R,
                                                    xbar_indices, xhat_indices)

    def test_lyapunov_derivative_as_milp3(self):
        # Test for system3
        # Now test with random x[n]
        torch.manual_seed(0)
        np.random.seed(0)
        x_samples = []
        R = torch.tensor([[1., 1], [-1., 1], [0, 1]], dtype=self.dtype)
        xbar_indices = None
        xhat_indices = None
        for _ in range(20):
            x_samples.append(self.sample_state(self.system3))

        for eps_type in list(lyapunov.ConvergenceEps):
            self.lyapunov_derivative_as_milp_tester(self.system3,
                                                    self.x_equilibrium3,
                                                    x_samples, eps_type, R,
                                                    xbar_indices, xhat_indices)

    def test_lyapunov_derivative_as_milp4(self):
        # Test with partial state, namely xbar_indices is not None.
        torch.manual_seed(0)
        lyap_relu = utils.setup_relu((3, 5, 1),
                                     params=None,
                                     negative_slope=0.1,
                                     bias=True,
                                     dtype=self.dtype)
        system = setup_relu_dyn_3d(self.dtype)
        V_lambda = 0.5
        dV_epsilon = 0.3
        R = torch.tensor([[0.5, 0.1], [-0.2, 0.4], [0.9, 1.2]],
                         dtype=self.dtype)
        xbar_indices = [0, 2]
        xhat_indices = [0, 1]
        for eps_type in list(lyapunov.ConvergenceEps):
            self.lyapunov_derivative_as_milp_check_state(
                system,
                system.x_equilibrium,
                lyap_relu,
                V_lambda,
                dV_epsilon,
                eps_type,
                R,
                fixed_R=True,
                xbar_indices=xbar_indices,
                xhat_indices=xhat_indices)

    def test_lyapunov_derivative_as_milp5(self):
        # Test with lp_relaxation=True, make sure the program contains no
        # binary variables.
        torch.manual_seed(0)
        lyap_relu = utils.setup_relu((3, 5, 1),
                                     params=None,
                                     negative_slope=0.1,
                                     bias=True,
                                     dtype=self.dtype)
        system = setup_relu_dyn_3d(self.dtype)
        V_lambda = 0.5
        dV_epsilon = 0.3
        R = torch.tensor([[0.5, 0.1, -0.7], [-0.2, 0.4, 0.3], [0.9, 1.2, 1.2]],
                         dtype=self.dtype)
        dut = lyapunov.LyapunovDiscreteTimeHybridSystem(system, lyap_relu)
        lp, x, beta, gamma, x_next, s, z, z_next, beta_next = \
            dut.lyapunov_derivative_as_milp(
                system.x_equilibrium, V_lambda, dV_epsilon,
                lyapunov.ConvergenceEps.ExpLower, R=R, fixed_R=True,
                lp_relaxation=True)
        for v in beta + gamma + beta_next:
            self.assertEqual(v.vtype, gurobipy.GRB.CONTINUOUS)
        self.assertEqual(lp.gurobi_model.numbinvars, 0)
        self.assertEqual(lp.gurobi_model.numintvars, 0)

    def lyapunov_derivative_as_milp_bounded_tester(self, system, lyapunov_relu,
                                                   x_equilibrium, V_lambda, R,
                                                   xbar_indices, xhat_indices):
        """
        Test lyapunov_derivative_as_milp function, but with a lower and upper
        bounds on V(x[n])
        """
        dut = lyapunov.LyapunovDiscreteTimeHybridSystem(system, lyapunov_relu)
        # First find out what is the lower and upper bound of the ReLU network.
        milp_relu = gurobi_torch_mip.GurobiTorchMILP(dut.system.dtype)
        relu1_free_pattern = relu_to_optimization.ReLUFreePattern(
            lyapunov_relu, dut.system.dtype)
        mip_constr_return, _, _, _, _, _, _ =\
            relu1_free_pattern.output_constraint(
                torch.from_numpy(system.x_lo_all),
                torch.from_numpy(system.x_up_all),
                mip_utils.PropagateBoundsMethod.IA)
        x = milp_relu.addVars(system.x_dim,
                              lb=-gurobipy.GRB.INFINITY,
                              vtype=gurobipy.GRB.CONTINUOUS)
        z, beta = milp_relu.add_mixed_integer_linear_constraints(
            mip_constr_return, x, None, "z", "beta", "", "", "")
        # Add λ* |R*(x̅ - x̅*)|₁. To do so, we introduce slack variable
        # s_x_norm, such that s_x_norm(i) = R[i, :] * (x̅ - x̅*).
        s_dim = R.shape[0]
        s_x_norm = milp_relu.addVars(s_dim,
                                     lb=-gurobipy.GRB.INFINITY,
                                     vtype=gurobipy.GRB.CONTINUOUS)
        beta_x_norm = milp_relu.addVars(s_dim, vtype=gurobipy.GRB.BINARY)
        xbar_indices = compute_xhat._get_xbar_indices(system.x_dim,
                                                      xbar_indices)
        s_lb, s_ub = mip_utils.compute_range_by_lp(
            R.detach().numpy(),
            (-R @ x_equilibrium[xbar_indices]).detach().numpy(),
            system.x_lo_all[xbar_indices], system.x_up_all[xbar_indices], None,
            None)
        for i in range(s_dim):
            Ain_x_norm, Ain_s_x_norm, Ain_beta_x_norm, rhs_x_norm = \
                utils.replace_absolute_value_with_mixed_integer_constraint(
                    s_lb[i], s_ub[i], system.dtype)
            # We add the constraint
            # Ain_x_norm * R[i, :] * (x̅-x̅*) + Ain_s_x_norm * s_x_norm(i) +
            # Ain_beta_x_norm * beta_x_norm(i) <= rhs_x_norm
            xbar_var = [x[i] for i in xbar_indices]
            milp_relu.addMConstrs([
                Ain_x_norm.reshape((-1, 1)) @ R[i].reshape((1, -1)),
                Ain_s_x_norm.reshape((-1, 1)),
                Ain_beta_x_norm.reshape((-1, 1))
            ], [xbar_var, [s_x_norm[i]], [beta_x_norm[i]]],
                                  sense=gurobipy.GRB.LESS_EQUAL,
                                  b=rhs_x_norm +
                                  Ain_x_norm.reshape((-1, 1)) @ R[i].reshape(
                                      (1, -1)) @ x_equilibrium[xbar_indices])
        # relu(x̂) = relu_xhat_coeff * relu_xhat_var + relu_xhat_constant
        if xhat_indices is None or xhat_indices == list(range(system.x_dim)):
            relu_xhat_var = []
            relu_xhat_coeff = []
            relu_xhat_constant = lyapunov_relu(x_equilibrium).squeeze()
        else:
            relu_xhat_slack, _, relu_xhat_a_out, relu_xhat_c_out, _, _, _ = \
                compute_xhat._compute_network_at_xhat(
                    milp_relu, x, x_equilibrium, relu1_free_pattern,
                    xhat_indices,
                    torch.from_numpy(system.x_lo_all),
                    torch.from_numpy(system.x_up_all),
                    mip_utils.PropagateBoundsMethod.IA,
                    lp_relaxation=False)
            relu_xhat_var = [relu_xhat_slack]
            relu_xhat_coeff = [relu_xhat_a_out.squeeze()]
            relu_xhat_constant = relu_xhat_c_out.squeeze()

        obj_coeff = [
            mip_constr_return.Aout_slack.squeeze(), V_lambda * torch.ones(
                (s_dim, ), dtype=system.dtype)
        ] + [-coeff for coeff in relu_xhat_coeff]
        obj_var = [z, s_x_norm] + relu_xhat_var
        obj_constant = float(mip_constr_return.Cout) - relu_xhat_constant
        milp_relu.setObjective(obj_coeff,
                               obj_var,
                               obj_constant,
                               sense=gurobipy.GRB.MAXIMIZE)
        milp_relu.gurobi_model.setParam(gurobipy.GRB.Param.OutputFlag, False)
        milp_relu.gurobi_model.optimize()
        self.assertEqual(milp_relu.gurobi_model.status,
                         gurobipy.GRB.Status.OPTIMAL)
        v_upper = milp_relu.gurobi_model.ObjVal
        x_sol = torch.tensor([v.x for v in x], dtype=dut.system.dtype)
        self.assertAlmostEqual(
            dut.lyapunov_value(x_sol,
                               x_equilibrium,
                               V_lambda,
                               R=R,
                               xbar_indices=xbar_indices,
                               xhat_indices=xhat_indices).item(), v_upper)
        milp_relu.setObjective(obj_coeff,
                               obj_var,
                               obj_constant,
                               sense=gurobipy.GRB.MINIMIZE)
        milp_relu.gurobi_model.optimize()
        self.assertEqual(milp_relu.gurobi_model.status,
                         gurobipy.GRB.Status.OPTIMAL)
        v_lower = milp_relu.gurobi_model.ObjVal
        x_sol = torch.tensor([v.x for v in x], dtype=dut.system.dtype)
        self.assertAlmostEqual(
            dut.lyapunov_value(x_sol,
                               x_equilibrium,
                               V_lambda,
                               R=R,
                               xbar_indices=xbar_indices,
                               xhat_indices=xhat_indices).item(), v_lower)

        # If we set lyapunov_lower to be v_upper + 1, the problem should be
        # infeasible.
        dV_epsilon = 0.01
        (milp, _, _, _, _, _, _, _, _) =\
            dut.lyapunov_derivative_as_milp(
                x_equilibrium, V_lambda, dV_epsilon,
                lyapunov.ConvergenceEps.ExpLower, R=R, fixed_R=True,
                lyapunov_lower=v_upper + 1, lyapunov_upper=v_upper + 2,
                xbar_indices=xbar_indices, xhat_indices=xhat_indices)
        milp.gurobi_model.setParam(gurobipy.GRB.Param.OutputFlag, False)
        milp.gurobi_model.setParam(gurobipy.GRB.Param.DualReductions, 0)
        milp.gurobi_model.optimize()
        self.assertEqual(milp.gurobi_model.status,
                         gurobipy.GRB.Status.INFEASIBLE)
        # If we set lyapunov_upper to be v_lower - 1, the problem should be
        # infeasible.
        (milp, _, _, _, _, _, _, _, _) =\
            dut.lyapunov_derivative_as_milp(
                x_equilibrium, V_lambda, dV_epsilon,
                lyapunov.ConvergenceEps.ExpLower, R=R, fixed_R=True,
                lyapunov_lower=v_lower - 2, lyapunov_upper=v_lower - 1,
                xbar_indices=xbar_indices, xhat_indices=xhat_indices)
        milp.gurobi_model.setParam(gurobipy.GRB.Param.OutputFlag, False)
        milp.gurobi_model.setParam(gurobipy.GRB.Param.DualReductions, 0)
        milp.gurobi_model.optimize()
        self.assertEqual(milp.gurobi_model.status,
                         gurobipy.GRB.Status.INFEASIBLE)

        # Now solve the MILP with valid lyapunov_lower and lyapunov_upper.
        # Then take many sample state. If lyapunov_lower <= V(x_sample) <=
        # lyapunov_upper, then the Lyapunov condition violation should be
        # smaller than milp optimal.
        lyapunov_lower = 0.9 * v_lower + 0.1 * v_upper
        lyapunov_upper = 0.1 * v_lower + 0.9 * v_upper
        (milp, _, _, _, _, _, _, _, _) =\
            dut.lyapunov_derivative_as_milp(
                x_equilibrium, V_lambda, dV_epsilon,
                lyapunov.ConvergenceEps.ExpLower, R=R, fixed_R=True,
                lyapunov_lower=lyapunov_lower, lyapunov_upper=lyapunov_upper,
                xbar_indices=xbar_indices, xhat_indices=xhat_indices)
        milp.gurobi_model.setParam(gurobipy.GRB.Param.OutputFlag, False)
        milp.gurobi_model.optimize()
        self.assertEqual(milp.gurobi_model.status, gurobipy.GRB.Status.OPTIMAL)
        x_samples = utils.uniform_sample_in_box(
            torch.from_numpy(system.x_lo_all),
            torch.from_numpy(system.x_up_all), 100)
        for i in range(x_samples.shape[0]):
            x_sample = x_samples[i]
            v = dut.lyapunov_value(x_sample,
                                   x_equilibrium,
                                   V_lambda,
                                   R=R,
                                   xbar_indices=xbar_indices,
                                   xhat_indices=xhat_indices)
            if v >= lyapunov_lower and v <= lyapunov_upper:
                x_next = system.step_forward(x_sample)
                v_next = dut.lyapunov_value(x_next,
                                            x_equilibrium,
                                            V_lambda,
                                            R=R,
                                            xbar_indices=xbar_indices,
                                            xhat_indices=xhat_indices)
                self.assertLessEqual(v_next - v + dV_epsilon * v,
                                     milp.gurobi_model.ObjVal)

    def test_lyapunov_derivative_as_milp_bounded(self):
        """
        Test lyapunov_derivative_as_milp function, but with a lower and upper
        bounds on V(x[n])
        """
        x_equilibrium = torch.tensor([0, 0], dtype=self.dtype)
        V_lambda = 0.01
        R = torch.tensor([[1, 1], [-1, 1], [0, 1]], dtype=self.dtype)
        xbar_indices = None
        xhat_indices = None

        lyapunov_relu1 = setup_relu(self.dtype)
        self.lyapunov_derivative_as_milp_bounded_tester(
            self.system1, lyapunov_relu1, x_equilibrium, V_lambda, R,
            xbar_indices, xhat_indices)

    def test_lyapunov_derivative_as_milp_bounded_partial_state(self):
        """
        Test lyapunov_derivative_as_milp function, but with a lower and upper
        bounds on V(x[n])
        """
        torch.manual_seed(0)
        lyapunov_relu = utils.setup_relu((3, 5, 1),
                                         params=None,
                                         negative_slope=0.1,
                                         bias=True,
                                         dtype=self.dtype)
        system = setup_relu_dyn_3d(self.dtype)
        x_equilibrium = torch.tensor([0.1, 0.2, 0.3], dtype=self.dtype)
        V_lambda = 0.1
        R = torch.tensor([[1, 1], [-1, 1], [0, 1]], dtype=self.dtype)
        xbar_indices = [1, 2]
        xhat_indices = [0, 2]

        self.lyapunov_derivative_as_milp_bounded_tester(
            system, lyapunov_relu, x_equilibrium, V_lambda, R, xbar_indices,
            xhat_indices)

    def compute_milp_cost_given_relu(self, system, weight_all, bias_all,
                                     requires_grad, eps_type, R, fixed_R):
        # Construct a simple ReLU model with 2 hidden layers
        assert (isinstance(weight_all, np.ndarray))
        assert (isinstance(bias_all, np.ndarray))
        assert (weight_all.shape == (22, ))
        assert (bias_all.shape == (8, ))
        weight_tensor = torch.from_numpy(weight_all).type(self.dtype)
        weight_tensor.requires_grad = requires_grad
        bias_tensor = torch.from_numpy(bias_all).type(self.dtype)
        bias_tensor.requires_grad = requires_grad
        linear1 = nn.Linear(2, 3)
        linear1.weight.data = weight_tensor[:6].clone().reshape((3, 2))
        linear1.bias.data = bias_tensor[:3].clone()
        linear2 = nn.Linear(3, 4)
        linear2.weight.data = weight_tensor[6:18].clone().reshape((4, 3))
        linear2.bias.data = bias_tensor[3:7].clone()
        linear3 = nn.Linear(4, 1)
        linear3.weight.data = weight_tensor[18:].clone().reshape((1, 4))
        linear3.bias.data = bias_tensor[7:].clone()
        lyapunov_relu1 = nn.Sequential(linear1, nn.ReLU(), linear2,
                                       nn.LeakyReLU(0.1), linear3)
        dut = lyapunov.LyapunovDiscreteTimeHybridSystem(system, lyapunov_relu1)
        R_torch = torch.from_numpy(R).reshape((3, 2))

        V_lambda = 0.1
        dV_epsilon = 0.01
        if not fixed_R:
            R_torch.requires_grad = requires_grad
        milp_return = dut.lyapunov_derivative_as_milp(torch.tensor(
            [0, 0], dtype=system.dtype),
                                                      V_lambda,
                                                      dV_epsilon,
                                                      eps_type,
                                                      R=R_torch,
                                                      fixed_R=fixed_R)
        milp = milp_return[0]

        milp.gurobi_model.setParam(gurobipy.GRB.Param.OutputFlag, False)
        milp.gurobi_model.optimize()
        objective = milp.compute_objective_from_mip_data_and_solution(
            penalty=0.)
        if requires_grad:
            objective.backward()
            weight_grad = np.concatenate(
                (linear1.weight.grad.detach().numpy().reshape(
                    (-1, )), linear2.weight.grad.detach().numpy().reshape(
                        (-1, )), linear3.weight.grad.detach().numpy().reshape(
                            (-1, ))),
                axis=0)
            bias_grad = np.concatenate(
                (linear1.bias.grad.detach().numpy().reshape(
                    (-1, )), linear2.bias.grad.detach().numpy().reshape(
                        (-1, )), linear3.bias.grad.detach().numpy().reshape(
                            (-1, ))),
                axis=0)
            if fixed_R:
                return (weight_grad, bias_grad)
            else:
                R_grad = R_torch.grad.reshape((-1)).detach().numpy()
                return (weight_grad, bias_grad, R_grad)
        else:
            np.testing.assert_allclose(milp.gurobi_model.ObjVal,
                                       objective.item(),
                                       atol=1e-5)
            return milp.gurobi_model.ObjVal

    def lyapunov_derivative_as_milp_gradient_tester(self, system, eps_type,
                                                    fixed_R, atol, rtol):
        """
        Test the gradient of the MILP optimal cost w.r.t the ReLU network
        weights and bias. I can first compute the gradient through pytorch
        autograd, and then compare that against numerical gradient.
        """
        # Test arbitrary weight and bias.
        weight_all_list = []
        bias_all_list = []
        weight_all_list.append(
            np.array([
                0.1, 0.5, -0.2, -2.5, 0.9, 4.5, -1.1, -2.4, 0.6, 12.5, 2.3,
                0.32, -2.9, 4.98, -1.23, 16.8, 0.54, 0.42, 1.54, 1.22, 2.1,
                -4.5
            ]))
        bias_all_list.append(
            np.array([0.45, -2.3, -4.3, 0.58, 2.45, 12.1, 4.6, -3.2]))
        weight_all_list.append(
            np.array([
                -0.3, 2.5, -3.2, -2.9, 4.9, 4.1, -1.1, -5.43, 0.9, 12.1, 9.3,
                4.32, -2.98, 4.92, 12.13, -16.8, 0.94, -4.42, 1.54, -13.22,
                29.1, -14.5
            ]))
        bias_all_list.append(
            np.array([2.45, -12.3, -4.9, 3.58, -2.15, 10.1, -4.6, -3.8]))
        weight_all_list.append(
            np.array([
                0.1, -0.2, 2, -0.4, 0.21, 3.2, 14.2, 47.1, 0.1, -2.5, 12.1,
                0.3, 0.5, -3.21, 0.75, 0.42, 3.45, 1.25, 2.41, 2.96, -3.22,
                -0.01
            ]))
        bias_all_list.append(
            np.array([0.25, 0.32, 0.34, -0.21, 0.46, 4.21, 12.4, -2.5]))
        weight_all_list.append(
            np.array([
                3.1, -1.3, 2.4, -2.4, 3.01, -3.1, 1.2, -41.3, 4.1, -2.4, 14.8,
                1.5, 2.5, -1.81, 3.78, 2.32, -.45, 2.25, 1.4, -.96, -3.95,
                -2.01
            ]))
        bias_all_list.append(
            np.array([4.25, 2.37, 0.39, -0.24, 1.49, -4.31, 82.5, -12.5]))

        # Do not use 0 in R matrix. This function is actually not
        # differentiable at R=0.
        R = np.array([1, 0.1, 1, -1.2, -0.1, 1.])
        # TODO(hongkai.dai): If I use
        # R = np.array([1, 0.1, 1, -3, -0.1, 1.]), then the gradient_test3
        # fails with gradient on weights/biases. Figure out why.

        for weight_all, bias_all in zip(weight_all_list, bias_all_list):
            grad_autodiff = self.compute_milp_cost_given_relu(
                system, weight_all, bias_all, True, eps_type, R, fixed_R)
            if fixed_R:
                grad_numerical = utils.compute_numerical_gradient(
                    lambda weight, bias: self.compute_milp_cost_given_relu(
                        system,
                        weight,
                        bias,
                        False,
                        eps_type,
                        R,
                        fixed_R=fixed_R),
                    weight_all,
                    bias_all,
                    dx=1e-6)
            else:
                grad_numerical = utils.compute_numerical_gradient(
                    lambda weight, bias, R: self.compute_milp_cost_given_relu(
                        system,
                        weight,
                        bias,
                        False,
                        eps_type,
                        R,
                        fixed_R=fixed_R),
                    weight_all,
                    bias_all,
                    R,
                    dx=1e-6)
            for i in range(len(grad_autodiff)):
                np.testing.assert_allclose(grad_autodiff[i],
                                           grad_numerical[i].squeeze(),
                                           atol=atol,
                                           rtol=rtol)

    def test_lyapunov_derivative_as_milp_gradient1(self):
        self.lyapunov_derivative_as_milp_gradient_tester(
            self.system1,
            lyapunov.ConvergenceEps.ExpLower,
            fixed_R=True,
            atol=3e-5,
            rtol=1e-7)

    def test_lyapunov_derivative_as_milp_gradient3(self):
        self.lyapunov_derivative_as_milp_gradient_tester(
            self.system3,
            lyapunov.ConvergenceEps.ExpLower,
            fixed_R=True,
            atol=6e-5,
            rtol=1e-7)

    def test_lyapunov_derivative_as_milp_gradient4(self):
        self.lyapunov_derivative_as_milp_gradient_tester(
            self.system4,
            lyapunov.ConvergenceEps.ExpLower,
            fixed_R=True,
            atol=1e-4,
            rtol=1.1e-4)

    def test_lyapunov_derivative_as_milp_gradient5(self):
        # Test with fixed_R=False
        self.lyapunov_derivative_as_milp_gradient_tester(
            self.system4,
            lyapunov.ConvergenceEps.ExpLower,
            fixed_R=False,
            atol=1e-4,
            rtol=1.1e-4)

    def test_lyapunov_derivative_loss_at_samples(self):
        # Construct a simple ReLU model with 2 hidden layers
        lyapunov_relu1 = setup_relu(torch.float64)
        lyapunov_relu2 = setup_leaky_relu(torch.float64)

        x_samples = []
        x_samples.append(torch.tensor([-0.5, -0.2], dtype=self.dtype))
        x_samples.append(torch.tensor([-0.5, 0.25], dtype=self.dtype))
        x_samples.append(torch.tensor([-0.7, -0.55], dtype=self.dtype))
        x_samples.append(torch.tensor([0.1, 0.85], dtype=self.dtype))
        x_samples.append(torch.tensor([0.45, 0.35], dtype=self.dtype))
        x_samples.append(torch.tensor([0.45, -0.78], dtype=self.dtype))
        x_samples.append(torch.tensor([0.95, -0.23], dtype=self.dtype))
        margin = 0.1
        x_equilibrium = torch.tensor([0, 0], dtype=self.dtype)
        V_lambda = 0.1
        R = torch.tensor([[1, 1], [-1, 1], [0, 1]], dtype=self.dtype)
        epsilon = 0.5
        for relu in (lyapunov_relu1, lyapunov_relu2):
            dut = lyapunov.LyapunovDiscreteTimeHybridSystem(self.system1, relu)
            for eps_type in list(lyapunov.ConvergenceEps):
                loss_expected = [None] * len(x_samples)
                for i, x_sample in enumerate(x_samples):
                    loss = dut.lyapunov_derivative_loss_at_samples(
                        V_lambda,
                        epsilon,
                        x_sample.unsqueeze(0),
                        x_equilibrium,
                        eps_type,
                        R=R,
                        margin=margin)
                    x_next = self.system1.step_forward(x_sample)
                    V_x_sample = dut.lyapunov_value(x_sample,
                                                    x_equilibrium,
                                                    V_lambda,
                                                    R=R)
                    V_x_next = dut.lyapunov_value(x_next,
                                                  x_equilibrium,
                                                  V_lambda,
                                                  R=R)
                    if eps_type == lyapunov.ConvergenceEps.ExpLower:
                        V_diff = V_x_next - V_x_sample + epsilon * V_x_sample
                    elif eps_type == lyapunov.ConvergenceEps.ExpUpper:
                        V_diff = -(V_x_next - V_x_sample +
                                   epsilon * V_x_sample)
                    elif eps_type == lyapunov.ConvergenceEps.Asymp:
                        V_diff = V_x_next - V_x_sample + epsilon * torch.norm(
                            R @ (x_sample - x_equilibrium), p=1)
                    loss_expected[i] = torch.max(
                        V_diff + margin, torch.tensor(0., dtype=self.dtype))
                    self.assertAlmostEqual(loss.item(),
                                           loss_expected[i].item())

                # Test for a batch of x.
                loss_batch = dut.lyapunov_derivative_loss_at_samples(
                    V_lambda,
                    epsilon,
                    torch.stack(x_samples),
                    x_equilibrium,
                    eps_type,
                    R=R,
                    margin=margin)
                loss_batch_expected = torch.mean(torch.cat(loss_expected))
                self.assertAlmostEqual(loss_batch.item(),
                                       loss_batch_expected.item())

                loss_max_batch = dut.lyapunov_derivative_loss_at_samples(
                    V_lambda,
                    epsilon,
                    torch.stack(x_samples),
                    x_equilibrium,
                    eps_type,
                    R=R,
                    margin=margin,
                    reduction="max")
                loss_max_batch_expected = torch.max(torch.cat(loss_expected))
                self.assertAlmostEqual(loss_max_batch.item(),
                                       loss_max_batch_expected.item())
                loss_4norm_batch = dut.lyapunov_derivative_loss_at_samples(
                    V_lambda,
                    epsilon,
                    torch.stack(x_samples),
                    x_equilibrium,
                    eps_type,
                    R=R,
                    margin=margin,
                    reduction="4norm")
                loss_4norm_batch_expected = torch.norm(
                    torch.cat(loss_expected), p=4)
                self.assertAlmostEqual(loss_4norm_batch.item(),
                                       loss_4norm_batch_expected.item())

                relu.zero_grad()
                loss_batch.backward()
                grad = [p.grad.data.clone() for p in relu.parameters()]
                relu.zero_grad()
                loss_batch_expected.backward()
                grad_expected = [
                    p.grad.data.clone() for p in relu.parameters()
                ]
                for i in range(len(grad)):
                    np.testing.assert_allclose(
                        grad[i].detach().numpy(),
                        grad_expected[i].detach().numpy(),
                        atol=1e-15)

    def test_lyapunov_derivative_loss_at_samples_partial_state(self):
        torch.manual_seed(0)
        lyap_relu = utils.setup_relu((3, 5, 1),
                                     params=None,
                                     negative_slope=0.1,
                                     bias=True,
                                     dtype=torch.float64)
        system = setup_relu_dyn_3d(self.dtype)
        dut = lyapunov.LyapunovDiscreteTimeHybridSystem(system, lyap_relu)
        V_lambda = 0.5
        epsilon = 0.3
        state_samples = torch.tensor(
            [[0.4, 0.1, 0.7], [-0.2, -0.5, 0.2], [-1.1, 0.4, 0.2]],
            dtype=self.dtype)
        xbar_indices = [0, 2]
        xhat_indices = [0, 1]
        margin = 0.01
        R = torch.tensor([[0.4, 0.3], [-1.2, 0.1], [0., 1.5]],
                         dtype=self.dtype)

        for eps_type in list(lyapunov.ConvergenceEps):
            loss = dut.lyapunov_derivative_loss_at_samples(
                V_lambda,
                epsilon,
                state_samples,
                system.x_equilibrium,
                eps_type,
                R=R,
                margin=margin,
                xbar_indices=xbar_indices,
                xhat_indices=xhat_indices)
            v = dut.lyapunov_value(state_samples,
                                   system.x_equilibrium,
                                   V_lambda,
                                   R=R,
                                   xbar_indices=xbar_indices,
                                   xhat_indices=xhat_indices)
            state_samples_next = system.step_forward(state_samples)
            v_next = dut.lyapunov_value(state_samples_next,
                                        system.x_equilibrium,
                                        V_lambda,
                                        R=R,
                                        xbar_indices=xbar_indices,
                                        xhat_indices=xhat_indices)
            if eps_type == lyapunov.ConvergenceEps.ExpLower:
                loss_expected = torch.nn.HingeEmbeddingLoss(margin=margin)(
                    -(v_next - v + epsilon * v), torch.tensor(-1.))
            elif eps_type == lyapunov.ConvergenceEps.ExpUpper:
                loss_expected = torch.nn.HingeEmbeddingLoss(margin=margin)(
                    (v_next - v + epsilon * v), torch.tensor(-1.))
            elif eps_type == lyapunov.ConvergenceEps.Asymp:
                loss_expected = torch.nn.HingeEmbeddingLoss(margin=margin)(
                    -(v_next - v + epsilon *
                      torch.norm(R @ (state_samples[:, xbar_indices] -
                                      system.x_equilibrium[xbar_indices]).T,
                                 p=1,
                                 dim=0)), torch.tensor(-1.))
            self.assertAlmostEqual(loss.item(), loss_expected.item())

    def test_lyapunov_as_milp_warmstart(self):
        def cb(model, where):
            if where == gurobipy.GRB.Callback.MIP:
                model.terminate()

        relu = setup_leaky_relu(self.dtype)
        x_equilibrium = self.x_equilibrium1
        V_epsilon = 0.01
        V_lambda = 0.1
        R = torch.tensor([[1, 1], [1, -1], [0, 1]], dtype=self.dtype)
        for system in [self.system1, self.system3]:
            dut = lyapunov.LyapunovDiscreteTimeHybridSystem(system, relu)
            (milp, x) = dut.lyapunov_positivity_as_milp(x_equilibrium,
                                                        V_lambda,
                                                        V_epsilon,
                                                        R=R,
                                                        fixed_R=True)
            milp.gurobi_model.setParam(gurobipy.GRB.Param.OutputFlag, False)
            milp.gurobi_model.setParam(gurobipy.GRB.Param.Presolve, 0)
            milp.gurobi_model.setParam(gurobipy.GRB.Param.Threads, 1)
            milp.gurobi_model.optimize()
            self.assertTrue(
                milp.gurobi_model.status == gurobipy.GRB.Status.OPTIMAL)
            x_sol1 = torch.tensor([var.X for var in x], dtype=self.dtype)
            (milp, x) = dut.lyapunov_positivity_as_milp(x_equilibrium,
                                                        V_lambda,
                                                        V_epsilon,
                                                        R=R,
                                                        fixed_R=True,
                                                        x_warmstart=x_sol1)
            milp.gurobi_model.setParam(gurobipy.GRB.Param.OutputFlag, False)
            milp.gurobi_model.setParam(gurobipy.GRB.Param.Presolve, 0)
            milp.gurobi_model.setParam(gurobipy.GRB.Param.Threads, 1)
            milp.gurobi_model.optimize(cb)
            self.assertTrue(
                milp.gurobi_model.status == gurobipy.GRB.Status.INTERRUPTED)
            self.assertEqual(milp.gurobi_model.solCount, 1)
            x_sol2 = torch.tensor([var.X for var in x], dtype=self.dtype)
            np.testing.assert_allclose(x_sol1.detach().numpy(),
                                       x_sol2.detach().numpy())
            (milp, x, _, _, _, _, _, _, _) = dut.lyapunov_derivative_as_milp(
                x_equilibrium,
                V_lambda,
                V_epsilon,
                lyapunov.ConvergenceEps.ExpLower,
                R=R,
                fixed_R=True)
            milp.gurobi_model.setParam(gurobipy.GRB.Param.OutputFlag, False)
            milp.gurobi_model.optimize()
            self.assertTrue(
                milp.gurobi_model.status == gurobipy.GRB.Status.OPTIMAL)
            x_sol3 = torch.tensor([var.X for var in x], dtype=self.dtype)
            (milp, x, _, _, _, _, _, _, _) = dut.lyapunov_derivative_as_milp(
                x_equilibrium,
                V_lambda,
                V_epsilon,
                lyapunov.ConvergenceEps.ExpLower,
                R=R,
                fixed_R=True,
                x_warmstart=x_sol3)
            milp.gurobi_model.setParam(gurobipy.GRB.Param.OutputFlag, False)
            milp.gurobi_model.optimize(cb)
            self.assertTrue(
                milp.gurobi_model.status == gurobipy.GRB.Status.INTERRUPTED)
            self.assertEqual(milp.gurobi_model.solCount, 1)
            x_sol4 = torch.tensor([var.X for var in x], dtype=self.dtype)
            np.testing.assert_allclose(x_sol3.detach().numpy(),
                                       x_sol4.detach().numpy())


if __name__ == "__main__":
    unittest.main()
