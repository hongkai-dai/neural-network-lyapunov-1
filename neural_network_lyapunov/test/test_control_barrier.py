import neural_network_lyapunov.control_barrier as mut
import neural_network_lyapunov.barrier as barrier
import neural_network_lyapunov.control_affine_system as control_affine_system
import neural_network_lyapunov.utils as utils
import neural_network_lyapunov.gurobi_torch_mip as gurobi_torch_mip
import neural_network_lyapunov.mip_utils as mip_utils

import torch
import numpy as np
import unittest
import gurobipy


class TestControlBarrier(unittest.TestCase):
    def setUp(self):
        self.dtype = torch.float64
        self.linear_system = control_affine_system.LinearSystem(
            torch.tensor([[1, 3], [2, -4]], dtype=self.dtype),
            torch.tensor([[1, 2, 3], [0, 1, -1]], dtype=self.dtype),
            x_lo=torch.tensor([-2, -3], dtype=self.dtype),
            x_up=torch.tensor([3, 1], dtype=self.dtype),
            u_lo=torch.tensor([-1, -3, 2], dtype=self.dtype),
            u_up=torch.tensor([2, -1, 4], dtype=self.dtype))
        phi_a = utils.setup_relu((2, 3, 1),
                                 params=None,
                                 negative_slope=0.1,
                                 bias=True,
                                 dtype=self.dtype)
        phi_a[0].weight.data = torch.tensor([[1, 3], [2, -1], [0, 1]],
                                            dtype=self.dtype)
        phi_a[0].bias.data = torch.tensor([0, 1, -2], dtype=self.dtype)
        phi_a[2].weight.data = torch.tensor([[1, -1, 2]], dtype=self.dtype)
        phi_a[2].bias.data = torch.tensor([2], dtype=self.dtype)
        phi_b = utils.setup_relu((2, 3, 3),
                                 params=None,
                                 negative_slope=0.1,
                                 bias=True,
                                 dtype=self.dtype)
        phi_b[0].weight.data = torch.tensor([[3, -1], [0, 2], [1, 1]],
                                            dtype=self.dtype)
        phi_b[0].bias.data = torch.tensor([1, -1, 2], dtype=self.dtype)
        phi_b[2].weight.data = torch.tensor(
            [[3, -1, 0], [2, 1, 1], [0, 1, -1]], dtype=self.dtype)
        phi_b[2].bias.data = torch.tensor([1, -1, 2], dtype=self.dtype)
        self.relu_system = \
            control_affine_system.ReluSecondOrderControlAffineSystem(
                x_lo=torch.tensor([-1, 1], dtype=self.dtype),
                x_up=torch.tensor([-0.5, 2], dtype=self.dtype),
                u_lo=torch.tensor([-1, -3, 1], dtype=self.dtype),
                u_up=torch.tensor([1, -1, 2], dtype=self.dtype),
                phi_a=phi_a,
                phi_b=phi_b,
                method=mip_utils.PropagateBoundsMethod.IA)
        self.barrier_relu1 = utils.setup_relu((2, 4, 3, 1),
                                              params=None,
                                              negative_slope=0.01,
                                              bias=True,
                                              dtype=self.dtype)
        self.barrier_relu1[0].weight.data = torch.tensor(
            [[1, -1], [0, 2], [1, 3], [-1, -2]], dtype=self.dtype)
        self.barrier_relu1[0].bias.data = torch.tensor([0, 1, -1, 2],
                                                       dtype=self.dtype)
        self.barrier_relu1[2].weight.data = torch.tensor(
            [[1, 0, -1, 2], [0, 2, -1, 1], [1, 0, 1, -2]], dtype=self.dtype)
        self.barrier_relu1[2].bias.data = torch.tensor([0, 2, 3],
                                                       dtype=self.dtype)
        self.barrier_relu1[4].weight.data = torch.tensor([[1, -3, 2]],
                                                         dtype=self.dtype)
        self.barrier_relu1[4].bias.data = torch.tensor([-1], dtype=self.dtype)

        self.barrier_relu2 = utils.setup_relu((2, 2, 1),
                                              params=None,
                                              negative_slope=0.1,
                                              bias=True,
                                              dtype=self.dtype)
        self.barrier_relu2[0].weight.data = torch.tensor([[1, 2], [3, 4]],
                                                         dtype=self.dtype)
        self.barrier_relu2[0].bias.data = torch.tensor([1, 2],
                                                       dtype=self.dtype)
        self.barrier_relu2[2].weight.data = torch.tensor([[1, 3]],
                                                         dtype=self.dtype)
        self.barrier_relu2[2].bias.data = torch.tensor([1], dtype=self.dtype)
        self.barrier_relu3 = utils.setup_relu((2, 2, 1),
                                              params=None,
                                              negative_slope=0.1,
                                              bias=True,
                                              dtype=self.dtype)
        self.barrier_relu3[0].weight.data = torch.tensor([[-1, -2], [-3, -4]],
                                                         dtype=self.dtype)
        self.barrier_relu3[0].bias.data = torch.tensor([1, 2],
                                                       dtype=self.dtype)
        self.barrier_relu3[2].weight.data = torch.tensor([[1, 3]],
                                                         dtype=self.dtype)
        self.barrier_relu3[2].bias.data = torch.tensor([1], dtype=self.dtype)

    def barrier_derivative_tester(self, dut, x, inf_norm_term):
        hdot = dut.barrier_derivative(x, inf_norm_term=inf_norm_term)

        relu_grad = utils.relu_network_gradient(dut.barrier_relu, x).squeeze(1)
        if inf_norm_term is not None:
            inf_norm_grad = utils.l_infinity_gradient(
                inf_norm_term.R @ x - inf_norm_term.p) @ inf_norm_term.R
            barrier_grad = utils.minikowski_sum(relu_grad, -inf_norm_grad)
        else:
            barrier_grad = relu_grad
        if barrier_grad.shape[0] == 1:
            x.requires_grad = True
            h = dut.barrier_value(x,
                                  torch.empty_like(x),
                                  c=100.,
                                  inf_norm_term=inf_norm_term)
            h.backward()
            np.testing.assert_allclose(barrier_grad[0].detach().numpy(),
                                       x.grad.detach().numpy())
            x.requires_grad = False
        f = dut.system.f(x)
        G = dut.system.G(x)
        hdot_expected = barrier_grad @ f
        dhdx_times_G = barrier_grad @ G
        for i in range(dut.system.u_dim):
            for j in range(barrier_grad.shape[0]):
                hdot_expected[j] += dhdx_times_G[
                    j, i] * dut.system.u_up[i] if dhdx_times_G[
                        j, i] >= 0 else dhdx_times_G[j, i] * dut.system.u_lo[i]
        self.assertEqual(hdot.shape, hdot_expected.shape)
        np.testing.assert_allclose(hdot.detach().numpy(),
                                   hdot_expected.detach().numpy())

    def test_barrier_derivative(self):
        torch.manual_seed(0)
        x_samples = utils.uniform_sample_in_box(self.linear_system.x_lo,
                                                self.linear_system.x_up, 100)
        dut1 = mut.ControlBarrier(self.linear_system, self.barrier_relu1)
        for i in range(x_samples.shape[0]):
            self.barrier_derivative_tester(dut1,
                                           x_samples[i],
                                           inf_norm_term=None)

        # Now test some x with multiple gradient.
        self.barrier_derivative_tester(dut1,
                                       torch.tensor([1, 1], dtype=self.dtype),
                                       inf_norm_term=None)
        self.barrier_derivative_tester(dut1,
                                       torch.tensor([1, 0], dtype=self.dtype),
                                       inf_norm_term=None)

        # Test with inf_norm_term
        for i in range(x_samples.shape[0]):
            self.barrier_derivative_tester(
                dut1, x_samples[i],
                barrier.InfNormTerm(
                    torch.tensor([[1, 3], [2, -1]], dtype=self.dtype),
                    torch.tensor([1, 5], dtype=self.dtype)))

    def compute_dhdx_times_G_tester(self, dut):
        milp = gurobi_torch_mip.GurobiTorchMIP(self.dtype)
        x = milp.addVars(dut.system.x_dim, lb=-gurobipy.GRB.INFINITY)
        barrier_mip_cnstr_return = dut.barrier_relu_free_pattern.\
            output_constraint(
                torch.from_numpy(dut.system.x_lo_all),
                torch.from_numpy(dut.system.x_up_all),
                dut.network_bound_propagate_method)
        _, barrier_relu_binary = milp.add_mixed_integer_linear_constraints(
            barrier_mip_cnstr_return, x, None, "", "", "", "", "",
            gurobipy.GRB.BINARY)
        f = milp.addVars(dut.system.x_dim, lb=-gurobipy.GRB.INFINITY)
        Gt = [None] * dut.system.u_dim
        for i in range(dut.system.u_dim):
            Gt[i] = milp.addVars(dut.system.x_dim, lb=-gurobipy.GRB.INFINITY)
        system_mip_cnstr_ret, _, _, _, _ = \
            control_affine_system.add_system_constraint(
                dut.system, milp, x, f, Gt,
                binary_var_type=gurobipy.GRB.BINARY)
        dhdx_times_G, dhdx_times_G_lo, dhdx_times_G_up = \
            dut._compute_dhdx_times_G(
                milp, x, barrier_relu_binary, Gt,
                system_mip_cnstr_ret.G_flat_lo,
                system_mip_cnstr_ret.G_flat_up, gurobipy.GRB.BINARY)
        milp.gurobi_model.setParam(gurobipy.GRB.Param.OutputFlag, False)
        torch.manual_seed(0)
        x_samples = utils.uniform_sample_in_box(dut.system.x_lo,
                                                dut.system.x_up, 100)
        for i in range(x_samples.shape[0]):
            for j in range(dut.system.x_dim):
                x[j].lb = x_samples[i][j].item()
                x[j].ub = x_samples[i][j].item()
            milp.gurobi_model.optimize()
            self.assertEqual(milp.gurobi_model.status,
                             gurobipy.GRB.Status.OPTIMAL)
            dhdx = utils.relu_network_gradient(dut.barrier_relu, x_samples[i])
            assert (dhdx.shape[0] == 1)
            G = dut.system.G(x_samples[i])
            dhdx_times_G_expected = dhdx[0][0] @ G
            np.testing.assert_allclose(np.array([v.x for v in dhdx_times_G]),
                                       dhdx_times_G_expected.detach().numpy())
            np.testing.assert_array_less(
                dhdx_times_G_expected.detach().numpy(),
                dhdx_times_G_up.detach().numpy() + 1E-6)
            np.testing.assert_array_less(
                dhdx_times_G_lo.detach().numpy(),
                dhdx_times_G_expected.detach().numpy() + 1E-6)

    def test_compute_dhdx_times_G(self):
        dut1 = mut.ControlBarrier(self.linear_system, self.barrier_relu1)
        self.compute_dhdx_times_G_tester(dut1)
        dut2 = mut.ControlBarrier(self.relu_system, self.barrier_relu1)
        self.compute_dhdx_times_G_tester(dut2)
        dut3 = mut.ControlBarrier(self.linear_system, self.barrier_relu2)
        self.compute_dhdx_times_G_tester(dut3)
        dut4 = mut.ControlBarrier(self.linear_system, self.barrier_relu3)
        self.compute_dhdx_times_G_tester(dut4)

    def add_dhdx_times_G_tester(self, dut):
        milp = gurobi_torch_mip.GurobiTorchMILP(self.dtype)
        x = milp.addVars(dut.system.x_dim, lb=-gurobipy.GRB.INFINITY)
        barrier_mip_cnstr_return = dut.barrier_relu_free_pattern.\
            output_constraint(
                torch.from_numpy(dut.system.x_lo_all),
                torch.from_numpy(dut.system.x_up_all),
                dut.network_bound_propagate_method)
        _, barrier_relu_binary = milp.add_mixed_integer_linear_constraints(
            barrier_mip_cnstr_return, x, None, "", "", "", "", "",
            gurobipy.GRB.BINARY)
        f = milp.addVars(dut.system.x_dim, lb=-gurobipy.GRB.INFINITY)
        Gt = [None] * dut.system.u_dim
        for i in range(dut.system.u_dim):
            Gt[i] = milp.addVars(dut.system.x_dim, lb=-gurobipy.GRB.INFINITY)
        system_mip_cnstr_ret, _, _, _, _ = \
            control_affine_system.add_system_constraint(
                dut.system, milp, x, f, Gt,
                binary_var_type=gurobipy.GRB.BINARY)
        cost_coeff = []
        cost_vars = []
        dhdx_times_G, dhdx_times_G_binary = dut._add_dhdx_times_G(
            milp, x, barrier_relu_binary, Gt, system_mip_cnstr_ret.G_flat_lo,
            system_mip_cnstr_ret.G_flat_up, cost_coeff, cost_vars,
            gurobipy.GRB.BINARY)
        milp.setObjective(cost_coeff,
                          cost_vars,
                          0.,
                          sense=gurobipy.GRB.MAXIMIZE)
        # Now sample many x, check the cost
        torch.manual_seed(0)
        x_samples = utils.uniform_sample_in_box(dut.system.x_lo,
                                                dut.system.x_up, 100)
        milp.gurobi_model.setParam(gurobipy.GRB.Param.OutputFlag, False)
        for i in range(x_samples.shape[0]):
            for j in range(dut.system.x_dim):
                x[j].lb = x_samples[i][j].item()
                x[j].ub = x_samples[i][j].item()
            milp.gurobi_model.optimize()
            self.assertEqual(milp.gurobi_model.status,
                             gurobipy.GRB.Status.OPTIMAL)
            # Compute -max_u ∂h/∂x * G * u
            #    s.t  u_lo <= u <= u_up
            # in the closed form
            dhdx = utils.relu_network_gradient(dut.barrier_relu, x_samples[i])
            assert (dhdx.shape[0] == 1)
            G = dut.system.G(x_samples[i])
            dhdx_times_G_expected = dhdx[0][0] @ G
            objective_expected = 0
            for j in range(dut.system.u_dim):
                objective_expected -= dhdx_times_G_expected[
                    j] * dut.system.u_up[j] if dhdx_times_G_expected[
                        j] >= 0 else dhdx_times_G_expected[
                            j] * dut.system.u_lo[j]
            self.assertAlmostEqual(milp.gurobi_model.ObjVal,
                                   objective_expected.item())
            np.testing.assert_allclose(np.array([v.x for v in dhdx_times_G]),
                                       dhdx_times_G_expected.detach().numpy())
            np.testing.assert_allclose(
                np.array([v.x for v in dhdx_times_G_binary]),
                (dhdx_times_G_expected >= 0).detach().numpy())

    def test_add_dhdx_times_G(self):
        self.linear_system.B = torch.tensor([[0.5, -0.1, 2], [1, 0.5, -1]],
                                            dtype=self.dtype)
        dut1 = mut.ControlBarrier(self.linear_system, self.barrier_relu1)
        self.add_dhdx_times_G_tester(dut1)
        dut2 = mut.ControlBarrier(self.relu_system, self.barrier_relu1)
        self.add_dhdx_times_G_tester(dut2)
        dut3 = mut.ControlBarrier(self.linear_system, self.barrier_relu2)
        self.add_dhdx_times_G_tester(dut3)
        dut4 = mut.ControlBarrier(self.linear_system, self.barrier_relu3)
        self.add_dhdx_times_G_tester(dut4)

    def barrier_derivative_as_milp_tester(self, dut, x_star, c, epsilon):
        barrier_deriv_ret = dut.barrier_derivative_as_milp(x_star, c, epsilon)
        barrier_deriv_ret.milp.gurobi_model.setParam(
            gurobipy.GRB.Param.OutputFlag, False)
        # Now maximize objective over x.
        barrier_deriv_ret.milp.gurobi_model.optimize()
        self.assertEqual(barrier_deriv_ret.milp.gurobi_model.status,
                         gurobipy.GRB.Status.OPTIMAL)
        x_optimal = torch.tensor([v.x for v in barrier_deriv_ret.x],
                                 dtype=self.dtype)
        self.assertAlmostEqual(
            barrier_deriv_ret.milp.gurobi_model.ObjVal,
            torch.max(-dut.barrier_derivative(x_optimal, zero_tol=1E-5) -
                      epsilon *
                      dut.barrier_value(x_optimal, x_star, c)).item())
        optimal_cost = barrier_deriv_ret.milp.gurobi_model.ObjVal
        # Sample many x, make sure the objective is correct and less than the
        # optimal cost.
        torch.manual_seed(0)
        x_samples = utils.uniform_sample_in_box(dut.system.x_lo,
                                                dut.system.x_up, 100)
        for i in range(x_samples.shape[0]):
            for j in range(dut.system.x_dim):
                barrier_deriv_ret.x[j].lb = x_samples[i][j].item()
                barrier_deriv_ret.x[j].ub = x_samples[i][j].item()
            barrier_deriv_ret.milp.gurobi_model.optimize()
            self.assertEqual(barrier_deriv_ret.milp.gurobi_model.status,
                             gurobipy.GRB.Status.OPTIMAL)
            objective_expected = -dut.barrier_derivative(
                x_samples[i]) - epsilon * dut.barrier_value(
                    x_samples[i], x_star, c)
            assert (objective_expected.shape[0] == 1)
            self.assertAlmostEqual(barrier_deriv_ret.milp.gurobi_model.ObjVal,
                                   objective_expected[0].item())
            self.assertLessEqual(barrier_deriv_ret.milp.gurobi_model.ObjVal,
                                 optimal_cost)

    def test_barrier_derivative_as_milp(self):
        dut1 = mut.ControlBarrier(self.linear_system, self.barrier_relu1)
        x_star = torch.tensor([0.5, 0.2], dtype=self.dtype)
        c = 0.3
        epsilon = 0.5
        self.barrier_derivative_as_milp_tester(dut1, x_star, c, epsilon)

        dut2 = mut.ControlBarrier(self.relu_system, self.barrier_relu2)
        self.barrier_derivative_as_milp_tester(dut2, x_star, c, epsilon)

        dut3 = mut.ControlBarrier(self.linear_system, self.barrier_relu2)
        self.barrier_derivative_as_milp_tester(dut3, x_star, c, epsilon)

        dut4 = mut.ControlBarrier(self.linear_system, self.barrier_relu3)
        self.barrier_derivative_as_milp_tester(dut4, x_star, c, epsilon)

    def test_barrier_derivative_given_action(self):
        dut1 = mut.ControlBarrier(self.linear_system, self.barrier_relu1)

        x_samples = utils.uniform_sample_in_box(dut1.system.x_lo,
                                                dut1.system.x_up, 100)
        u_samples = utils.uniform_sample_in_box(dut1.system.u_lo,
                                                dut1.system.u_up, 100)
        hdot_batch = dut1.barrier_derivative_given_action(x_samples, u_samples)
        self.assertEqual(hdot_batch.shape, (x_samples.shape[0], ))
        for i in range(x_samples.shape[0]):
            xdot = dut1.system.dynamics(x_samples[i], u_samples[i])
            dhdx = utils.relu_network_gradient(dut1.barrier_relu,
                                               x_samples[i]).squeeze(1)
            hdot_expected = torch.min(dhdx @ xdot)
            self.assertAlmostEqual(
                hdot_expected.item(),
                dut1.barrier_derivative_given_action(x_samples[i],
                                                     u_samples[i]).item())
            self.assertAlmostEqual(hdot_batch[i].item(), hdot_expected)

        # Test x such that dhdx has multiple values.
        x = torch.tensor([1, 1], dtype=self.dtype)
        u = torch.tensor([1, 2, 3], dtype=self.dtype)
        hdot = dut1.barrier_derivative_given_action(x, u)
        dhdx = utils.relu_network_gradient(dut1.barrier_relu, x).squeeze(1)
        hdot_expected = torch.min(dhdx @ dut1.system.dynamics(x, u))
        self.assertAlmostEqual(hdot.item(), hdot_expected.item())


if __name__ == "__main__":
    unittest.main()
