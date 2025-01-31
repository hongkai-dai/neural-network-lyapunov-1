import neural_network_lyapunov.lyapunov as lyapunov

import neural_network_lyapunov.hybrid_linear_system as hybrid_linear_system
import neural_network_lyapunov.utils as utils
import neural_network_lyapunov.relu_system as relu_system

import unittest
import numpy as np
import torch
import gurobipy


class TestLyapunovDiscreteTimeHybridSystemROA(unittest.TestCase):
    """
    This tests computing the region of attraction given the Lyapunov function
    and the verified region.
    """
    def setUp(self):
        # Define three dynamical systems
        # System 1 has all the states contracting, so if x[n] is within the
        # box x_lo <= x[n] <= x_up, then x[n+1] is guaranteed to be within the
        # box.
        # System 2 has all the states expanding, so if x[n] is outside of the
        # box, x[n+1] is guaranteed to be outside of the box.
        # System 3 has some states contracting, and some states expanding.
        self.dtype = torch.float64
        self.system1 = hybrid_linear_system.AutonomousHybridLinearSystem(
            2, self.dtype)
        self.system2 = hybrid_linear_system.AutonomousHybridLinearSystem(
            2, self.dtype)
        self.system3 = hybrid_linear_system.AutonomousHybridLinearSystem(
            2, self.dtype)
        self.x_equilibrium = torch.zeros((2, ), dtype=self.dtype)

        def _add_mode1(system, A):
            system.add_mode(
                A, torch.zeros((2, ), dtype=self.dtype),
                torch.cat((torch.eye(
                    2, dtype=self.dtype), -torch.eye(2, dtype=self.dtype)),
                          dim=0), torch.tensor([1, 1, 0, 1], dtype=self.dtype))

        def _add_mode2(system, A):
            system.add_mode(
                A, torch.zeros((2, ), dtype=self.dtype),
                torch.cat((torch.eye(
                    2, dtype=self.dtype), -torch.eye(2, dtype=self.dtype)),
                          dim=0), torch.tensor([0, 1, 1, 1], dtype=self.dtype))

        _add_mode1(self.system1,
                   torch.tensor([[0.5, 0], [0, 0.2]], dtype=self.dtype))
        _add_mode2(self.system1,
                   torch.tensor([[0.2, 0], [0, 0.5]], dtype=self.dtype))
        _add_mode1(self.system2,
                   torch.tensor([[1.5, 0], [0, 1.2]], dtype=self.dtype))
        _add_mode2(self.system2,
                   torch.tensor([[1.2, 0], [0, 1.5]], dtype=self.dtype))
        _add_mode1(self.system3,
                   torch.tensor([[1.5, 0], [0, 1.2]], dtype=self.dtype))
        _add_mode2(self.system3,
                   torch.tensor([[0.2, 0], [0, 0.5]], dtype=self.dtype))

        self.lyap_relu = utils.setup_relu((2, 4, 1),
                                          params=None,
                                          bias=True,
                                          negative_slope=0.1,
                                          dtype=self.dtype)
        self.lyap_relu[0].weight.data = torch.tensor(
            [[1.5, 0.3], [0.2, -0.4], [1.2, -0.4], [0.7, 0.1]],
            dtype=self.dtype)
        self.lyap_relu[0].bias.data = torch.tensor([0.4, -0.3, 1.1, 0.5],
                                                   dtype=self.dtype)
        self.lyap_relu[2].weight.data = torch.tensor([[1., 0.5, -0.3, 1.2]],
                                                     dtype=self.dtype)
        self.lyap_relu[2].bias.data = torch.tensor([0.3], dtype=self.dtype)

    def construct_milp_roa_tester(self, dut, x_curr_in_box, is_milp_feasible):
        V_lambda = 0.5
        R = torch.tensor([[1, 3], [0.5, -1]], dtype=self.dtype)
        x_lo_larger = np.array([-10, -10.])
        x_up_larger = np.array([10., 10.])
        # x_curr inside the box, and x_next outside the box.
        milp, x_curr, x_next, t_slack, box_zeta = dut._construct_milp_for_roa(
            V_lambda, R, self.x_equilibrium, x_lo_larger, x_up_larger,
            x_curr_in_box)
        milp.gurobi_model.setParam(gurobipy.GRB.Param.OutputFlag, False)
        milp.gurobi_model.setParam(gurobipy.GRB.Param.DualReductions, False)
        milp.gurobi_model.optimize()
        if is_milp_feasible:
            self.assertEqual(milp.gurobi_model.status,
                             gurobipy.GRB.Status.OPTIMAL)
            x_curr_val = np.array([v.x for v in x_curr])
            x_next_val = np.array([v.x for v in x_next])
            self.assertAlmostEqual(
                dut.lyapunov_value(torch.from_numpy(x_curr_val),
                                   self.x_equilibrium,
                                   V_lambda,
                                   R=R).item(), milp.gurobi_model.ObjVal)
            if x_curr_in_box:
                in_box_x = x_curr_val
                out_box_x = x_next_val
            else:
                in_box_x = x_next_val
                out_box_x = x_curr_val
            np.testing.assert_array_less(in_box_x, dut.system.x_up_all + 1E-7)
            np.testing.assert_array_less(dut.system.x_lo_all - 1E-7, in_box_x)
            self.assertFalse(
                np.all(out_box_x <= dut.system.x_up_all - 1E-7)
                and np.all(out_box_x >= dut.system.x_lo_all + 1E-7))
            for i in range(len(t_slack)):
                if box_zeta[i].x > 1 - 1E-7:
                    np.testing.assert_allclose(
                        np.array([v.x for v in t_slack[i]]), out_box_x)
                else:
                    np.testing.assert_allclose(
                        np.array([v.x for v in t_slack[i]]),
                        np.zeros((dut.system.x_dim, )))
        else:
            self.assertEqual(milp.gurobi_model.status,
                             gurobipy.GRB.Status.INFEASIBLE)

    def test_construct_milp_for_roa1(self):
        dut = lyapunov.LyapunovDiscreteTimeHybridSystem(
            self.system1, self.lyap_relu)
        # Since system1 has contracting states, it is impossible to have
        # x_curr inside the box while x_next outside the box.
        self.construct_milp_roa_tester(dut,
                                       x_curr_in_box=True,
                                       is_milp_feasible=False)
        # x_curr outside of the box, and x_next inside the box.
        self.construct_milp_roa_tester(dut,
                                       x_curr_in_box=False,
                                       is_milp_feasible=True)

    def test_construct_milp_for_roa2(self):
        dut = lyapunov.LyapunovDiscreteTimeHybridSystem(
            self.system2, self.lyap_relu)
        # x_curr inside the box, x_next outside the box
        self.construct_milp_roa_tester(dut,
                                       x_curr_in_box=True,
                                       is_milp_feasible=True)
        # Since system2 has expanding states, it is impossible to have x_curr
        # outside of the box, and x_next inside the box.
        self.construct_milp_roa_tester(dut,
                                       x_curr_in_box=False,
                                       is_milp_feasible=False)

    def test_construct_milp_for_roa3(self):
        dut = lyapunov.LyapunovDiscreteTimeHybridSystem(
            self.system3, self.lyap_relu)
        # x_curr inside the box, x_next outside the box.
        self.construct_milp_roa_tester(dut,
                                       x_curr_in_box=True,
                                       is_milp_feasible=True)
        # x_curr outside the box, x_next inside the box.
        self.construct_milp_roa_tester(dut,
                                       x_curr_in_box=False,
                                       is_milp_feasible=True)

    def test_compute_region_of_attraction1(self):
        dut = lyapunov.LyapunovDiscreteTimeHybridSystem(
            self.system3, self.lyap_relu)
        V_lambda = 0.5
        R = torch.tensor([[1., 2.], [0.5, -1.]], dtype=self.dtype)
        x_lo_larger = torch.tensor([-5, -5], dtype=self.dtype)
        x_up_larger = torch.tensor([5, 5], dtype=self.dtype)
        rho = dut.compute_region_of_attraction(V_lambda, R, self.x_equilibrium,
                                               None, x_lo_larger, x_up_larger)

        milp2, _, _, _, _ = dut._construct_milp_for_roa(
            V_lambda, R, self.x_equilibrium, x_lo_larger, x_up_larger, False)
        milp2.gurobi_model.setParam(gurobipy.GRB.Param.OutputFlag, False)
        milp2.gurobi_model.optimize()
        self.assertEqual(rho, milp2.gurobi_model.ObjVal)

    def test_compute_region_of_attraction3(self):
        dut = lyapunov.LyapunovDiscreteTimeHybridSystem(
            self.system3, self.lyap_relu)
        V_lambda = 0.5
        R = torch.tensor([[1., 2.], [0.5, -1.]], dtype=self.dtype)
        x_lo_larger = torch.tensor([-5, -5], dtype=self.dtype)
        x_up_larger = torch.tensor([5, 5], dtype=self.dtype)
        rho = dut.compute_region_of_attraction(V_lambda, R, self.x_equilibrium,
                                               None, x_lo_larger, x_up_larger)

        milp1, _, _, _, _ = dut._construct_milp_for_roa(
            V_lambda, R, self.x_equilibrium, x_lo_larger, x_up_larger, True)
        milp1.gurobi_model.setParam(gurobipy.GRB.Param.OutputFlag, False)
        milp1.gurobi_model.optimize()

        milp2, _, _, _, _ = dut._construct_milp_for_roa(
            V_lambda, R, self.x_equilibrium, x_lo_larger, x_up_larger, False)
        milp2.gurobi_model.setParam(gurobipy.GRB.Param.OutputFlag, False)
        milp2.gurobi_model.optimize()
        self.assertEqual(
            rho, np.min([milp1.gurobi_model.ObjVal,
                         milp2.gurobi_model.ObjVal]))


class TestLyapunovHybridSystemROABoundary(unittest.TestCase):
    def setUp(self):
        self.dtype = torch.float64
        torch.manual_seed(0)
        lyapunov_relu = utils.setup_relu((2, 5, 6, 3, 1),
                                         params=None,
                                         negative_slope=0.1,
                                         bias=True,
                                         dtype=self.dtype)
        forward_relu = utils.setup_relu((2, 4, 2),
                                        params=None,
                                        negative_slope=0.1,
                                        bias=True,
                                        dtype=self.dtype)
        self.x_lo = torch.tensor([-2, -3], dtype=self.dtype)
        self.x_up = torch.tensor([3, 5], dtype=self.dtype)
        forward_system = relu_system.AutonomousReLUSystem(
            self.dtype, self.x_lo, self.x_up, forward_relu)
        self.dut = lyapunov.LyapunovDiscreteTimeHybridSystem(
            forward_system, lyapunov_relu)

    def construct_milp_for_roa_boundary(self, V_lambda, R, x_equilibrium):
        milp, x = self.dut._construct_milp_for_roa_boundary(
            V_lambda, R, x_equilibrium)
        milp.gurobi_model.setParam(gurobipy.GRB.Param.OutputFlag, False)
        milp.gurobi_model.optimize()
        self.assertEqual(milp.gurobi_model.status, gurobipy.GRB.Status.OPTIMAL)
        x_sol = torch.tensor([v.x for v in x], dtype=self.dtype)
        # Check if the optimal solution is on the boundary.
        self.assertTrue(
            torch.logical_or(torch.any(torch.abs(x_sol - self.x_lo) < 1E-6),
                             torch.any(torch.abs(x_sol - self.x_up) < 1E-6)))
        # Check if rho is computed correctly.
        rho = milp.gurobi_model.ObjVal
        self.assertAlmostEqual(self.dut.lyapunov_value(x_sol,
                                                       x_equilibrium,
                                                       V_lambda,
                                                       R=R).item(),
                               rho,
                               places=6)
        # Now sample many states on the boundary, make sure V evaluated at
        # these states are all above rho.
        x_samples1 = utils.uniform_sample_in_box(
            torch.tensor([self.x_lo[0], self.x_lo[1]], dtype=self.dtype),
            torch.tensor([self.x_lo[0], self.x_up[1]], dtype=self.dtype), 1000)
        x_samples2 = utils.uniform_sample_in_box(
            torch.tensor([self.x_up[0], self.x_lo[1]], dtype=self.dtype),
            torch.tensor([self.x_up[0], self.x_up[1]], dtype=self.dtype), 1000)
        x_samples3 = utils.uniform_sample_in_box(
            torch.tensor([self.x_lo[0], self.x_lo[1]], dtype=self.dtype),
            torch.tensor([self.x_up[0], self.x_lo[1]], dtype=self.dtype), 1000)
        x_samples4 = utils.uniform_sample_in_box(
            torch.tensor([self.x_lo[0], self.x_up[1]], dtype=self.dtype),
            torch.tensor([self.x_up[0], self.x_up[1]], dtype=self.dtype), 1000)
        x_samples = torch.cat((x_samples1, x_samples2, x_samples3, x_samples4),
                              dim=0)
        with torch.no_grad():
            V_samples = self.dut.lyapunov_value(x_samples,
                                                x_equilibrium,
                                                V_lambda,
                                                R=R)
        np.testing.assert_array_less(rho - 1E-6, V_samples.detach().numpy())

    def test_lyapunov_relu1(self):
        self.dut.lyapunov_relu[0].weight.data = torch.tensor(
            [[2, 4], [-1, 2], [0, 5], [-1, -3], [2, 4]], dtype=self.dtype)
        self.construct_milp_for_roa_boundary(V_lambda=0.5,
                                             R=torch.eye(2, dtype=self.dtype),
                                             x_equilibrium=torch.tensor(
                                                 [0, 0], dtype=self.dtype))

    def test_lyapunov_relu2(self):
        self.dut.lyapunov_relu[0].weight.data = torch.tensor(
            [[3, -4], [-4, 1], [0, 4], [-2, -3], [2, 4]], dtype=self.dtype)
        self.construct_milp_for_roa_boundary(
            V_lambda=0.5,
            R=torch.tensor([[0, 1], [-1, 3], [2, 0]], dtype=self.dtype),
            x_equilibrium=torch.tensor([1, 2], dtype=self.dtype))


if __name__ == "__main__":
    unittest.main()
