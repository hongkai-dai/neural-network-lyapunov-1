import neural_network_lyapunov.train_barrier as mut
import neural_network_lyapunov.barrier as barrier
import neural_network_lyapunov.control_affine_system as control_affine_system
import neural_network_lyapunov.control_barrier as control_barrier
import neural_network_lyapunov.gurobi_torch_mip as gurobi_torch_mip
import neural_network_lyapunov.utils as utils
import neural_network_lyapunov.mip_utils as mip_utils
import neural_network_lyapunov.nominal_controller as nominal_controller

import torch
import unittest
import numpy as np


class TestTrainBarrier(unittest.TestCase):
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

    def total_loss_tester(self, dut, unsafe_mip_cost_weight,
                          verify_region_boundary_mip_cost_weight,
                          barrier_deriv_mip_cost_weight, unsafe_state_samples,
                          boundary_state_samples, deriv_state_samples):
        total_loss_return = dut.total_loss(
            unsafe_mip_cost_weight, verify_region_boundary_mip_cost_weight,
            barrier_deriv_mip_cost_weight, unsafe_state_samples,
            boundary_state_samples, deriv_state_samples)

        loss_expected = torch.tensor(0, dtype=self.dtype)
        unsafe_mip_ret = dut.barrier_system.barrier_value_as_milp(
            dut.x_star,
            dut.c,
            dut.unsafe_region_cnstr,
            inf_norm_term=dut.inf_norm_term)
        for param, val in dut.unsafe_mip_params.items():
            unsafe_mip_ret.milp.gurobi_model.setParam(param, val)
        unsafe_mip_ret.milp.gurobi_model.optimize()
        self.assertEqual(total_loss_return.unsafe_mip_objective,
                         unsafe_mip_ret.milp.gurobi_model.ObjVal)
        loss_expected += unsafe_mip_cost_weight * torch.maximum(
            torch.tensor(0, dtype=self.dtype),
            unsafe_mip_ret.milp.compute_objective_from_mip_data_and_solution(
                solution_number=0, penalty=1E-13) + dut.unsafe_mip_margin)

        boundary_mip_ret = dut.barrier_system.barrier_value_as_milp(
            dut.x_star,
            dut.c,
            dut.verify_region_boundary,
            inf_norm_term=dut.inf_norm_term)
        for param, val in dut.verify_region_boundary_mip_params.items():
            boundary_mip_ret.milp.gurobi_model.setParam(param, val)
        boundary_mip_ret.milp.gurobi_model.optimize()
        self.assertEqual(
            total_loss_return.verify_region_boundary_mip_objective,
            boundary_mip_ret.milp.gurobi_model.ObjVal)
        loss_expected += verify_region_boundary_mip_cost_weight *\
            torch.maximum(
                torch.tensor(0, dtype=self.dtype),
                boundary_mip_ret.milp.
                compute_objective_from_mip_data_and_solution(
                    solution_number=0, penalty=1E-13)
                + dut.boundary_mip_margin)

        barrier_deriv_mip_ret = dut.barrier_system.barrier_derivative_as_milp(
            dut.x_star, dut.c, dut.epsilon, inf_norm_term=dut.inf_norm_term)
        for param, val in dut.barrier_deriv_mip_params.items():
            barrier_deriv_mip_ret.milp.gurobi_model.setParam(param, val)
        barrier_deriv_mip_ret.milp.gurobi_model.optimize()
        self.assertEqual(total_loss_return.barrier_deriv_mip_objective,
                         barrier_deriv_mip_ret.milp.gurobi_model.ObjVal)
        loss_expected += barrier_deriv_mip_cost_weight * torch.maximum(
            torch.tensor(0, dtype=self.dtype),
            barrier_deriv_mip_ret.milp.
            compute_objective_from_mip_data_and_solution(
                solution_number=0, penalty=1E-13) + dut.deriv_mip_margin)
        sample_loss = dut.compute_sample_loss(
            unsafe_state_samples, boundary_state_samples, deriv_state_samples,
            dut.unsafe_state_samples_weight, dut.boundary_state_samples_weight,
            dut.derivative_state_samples_weight)
        loss_expected += sample_loss

        if dut.nominal_control_option is not None:
            loss_expected += dut.nominal_controller_loss()

        self.assertAlmostEqual(total_loss_return.loss.item(),
                               loss_expected.item())

    def test_total_loss(self):
        c = 0.5
        epsilon = 0.5

        torch.manual_seed(0)
        boundary_state_samples = utils.uniform_sample_on_box_boundary(
            self.linear_system.x_lo, self.linear_system.x_up, 100)
        deriv_state_samples = utils.uniform_sample_in_box(
            self.linear_system.x_lo, self.linear_system.x_up, 20)
        inf_norm_term = barrier.InfNormTerm(
            torch.tensor([[1, -2], [1, 0.5], [0, 0.1]], dtype=self.dtype),
            torch.tensor([0.5, 0.2, 0.1], dtype=self.dtype))

        for barrier_relu in (self.barrier_relu1, self.barrier_relu2,
                             self.barrier_relu3):
            x_star = torch.tensor([0.5, 0.1], dtype=self.dtype)
            unsafe_region_cnstr1 = \
                gurobi_torch_mip.MixedIntegerConstraintsReturn()
            unsafe_region_cnstr1.Ain_input = torch.tensor([[1, 0]],
                                                          dtype=self.dtype)
            unsafe_region_cnstr1.rhs_in = torch.tensor([1], dtype=self.dtype)
            unsafe_state_samples = torch.tensor([[-2, 0], [0, 1]],
                                                dtype=self.dtype)
            dut = mut.TrainBarrier(
                control_barrier.ControlBarrier(self.linear_system,
                                               barrier_relu), x_star, c,
                unsafe_region_cnstr1,
                utils.box_boundary(self.linear_system.x_lo,
                                   self.linear_system.x_up), epsilon,
                inf_norm_term)
            dut.unsafe_state_samples_weight = 3.
            dut.derivative_state_samples_weight = 5.
            dut.boundary_state_samples_weight = 2.
            dut.unsafe_mip_margin = 10.
            dut.boundary_mip_margin = 20.
            dut.deriv_mip_margin = 30.
            self.total_loss_tester(dut, 2., 3., 4., unsafe_state_samples,
                                   boundary_state_samples, deriv_state_samples)

            controller_nn = utils.setup_relu((2, 4, 3),
                                             params=None,
                                             negative_slope=0.1,
                                             bias=True)
            u_star = torch.zeros((3, ), dtype=self.dtype)
            controller = nominal_controller.NominalNNController(
                controller_nn, x_star, u_star, self.linear_system.u_lo,
                self.linear_system.u_up)
            sample_states = utils.uniform_sample_in_box(
                self.linear_system.x_lo, self.linear_system.x_up, 100)
            nominal_control_option = mut.NominalControlOption(
                controller,
                sample_states,
                weight=1.5,
                margin=0.5,
                norm="mean",
                nominal_control_loss_tol=0.5)
            dut = mut.TrainBarrier(
                control_barrier.ControlBarrier(self.linear_system,
                                               barrier_relu), x_star, c,
                unsafe_region_cnstr1,
                utils.box_boundary(self.linear_system.x_lo,
                                   self.linear_system.x_up), epsilon,
                inf_norm_term, nominal_control_option)
            self.total_loss_tester(dut, 2., 3., 4., unsafe_state_samples,
                                   boundary_state_samples, deriv_state_samples)

        for barrier_relu in (self.barrier_relu1, self.barrier_relu2,
                             self.barrier_relu3):
            x_star = torch.tensor([-0.6, 1.7], dtype=self.dtype)
            unsafe_region_cnstr2 = \
                gurobi_torch_mip.MixedIntegerConstraintsReturn()
            unsafe_region_cnstr2.Ain_input = torch.tensor([[0, 1]],
                                                          dtype=self.dtype)
            unsafe_region_cnstr2.rhs_in = torch.tensor([1.5], dtype=self.dtype)
            unsafe_state_samples = torch.tensor([[0, 1], [0.5, 0]],
                                                dtype=self.dtype)
            dut = mut.TrainBarrier(
                control_barrier.ControlBarrier(self.relu_system, barrier_relu),
                x_star, c, unsafe_region_cnstr2,
                utils.box_boundary(self.relu_system.x_lo,
                                   self.relu_system.x_up), epsilon)
            self.total_loss_tester(dut, 2., 3., 4., unsafe_state_samples,
                                   boundary_state_samples, deriv_state_samples)

    def test_train(self):
        c = 0.5
        epsilon = 0.5

        torch.manual_seed(0)
        boundary_state_samples = utils.uniform_sample_on_box_boundary(
            self.linear_system.x_lo, self.linear_system.x_up, 100)
        deriv_state_samples = utils.uniform_sample_in_box(
            self.linear_system.x_lo, self.linear_system.x_up, 10)
        inf_norm_term = barrier.InfNormTerm(
            torch.diag(2. /
                       (self.linear_system.x_up - self.linear_system.x_lo)),
            (self.linear_system.x_up + self.linear_system.x_lo) /
            (self.linear_system.x_up - self.linear_system.x_lo))

        for barrier_relu in (self.barrier_relu1, self.barrier_relu2,
                             self.barrier_relu3):
            x_star = torch.tensor([0.5, 0.1], dtype=self.dtype)
            unsafe_region_cnstr1 = \
                gurobi_torch_mip.MixedIntegerConstraintsReturn()
            unsafe_region_cnstr1.Ain_input = torch.tensor([[1, 0]],
                                                          dtype=self.dtype)
            unsafe_region_cnstr1.rhs_in = torch.tensor([1], dtype=self.dtype)
            unsafe_state_samples = torch.tensor([[1, 0]], dtype=self.dtype)
            dut = mut.TrainBarrier(
                control_barrier.ControlBarrier(self.linear_system,
                                               barrier_relu), x_star, c,
                unsafe_region_cnstr1,
                utils.box_boundary(self.linear_system.x_lo,
                                   self.linear_system.x_up), epsilon,
                inf_norm_term)
            dut.max_iterations = 3
            dut.train(unsafe_state_samples, boundary_state_samples,
                      deriv_state_samples)

    def compute_sample_loss_tester(self, dut, unsafe_state_samples,
                                   boundary_state_samples,
                                   derivative_state_samples,
                                   unsafe_state_samples_weight,
                                   boundary_state_samples_weight,
                                   derivative_state_samples_weight):
        total_loss = dut.compute_sample_loss(unsafe_state_samples,
                                             boundary_state_samples,
                                             derivative_state_samples,
                                             unsafe_state_samples_weight,
                                             boundary_state_samples_weight,
                                             derivative_state_samples_weight)
        total_loss_expected = torch.tensor(0, dtype=self.dtype)
        if unsafe_state_samples_weight is not None:
            h_unsafe = dut.barrier_system.barrier_value(
                unsafe_state_samples,
                dut.x_star,
                dut.c,
                inf_norm_term=dut.inf_norm_term)
            total_loss_expected += unsafe_state_samples_weight * torch.mean(
                torch.maximum(h_unsafe,
                              torch.zeros_like(h_unsafe, dtype=self.dtype)))
        if boundary_state_samples_weight is not None:
            h_boundary = dut.barrier_system.barrier_value(
                boundary_state_samples,
                dut.x_star,
                dut.c,
                inf_norm_term=dut.inf_norm_term)
            total_loss_expected += boundary_state_samples_weight * torch.mean(
                torch.maximum(h_boundary,
                              torch.zeros_like(h_boundary, dtype=self.dtype)))
        if derivative_state_samples_weight is not None:
            hdot = torch.stack([
                torch.min(
                    dut.barrier_system.barrier_derivative(
                        derivative_state_samples[i],
                        inf_norm_term=dut.inf_norm_term))
                for i in range(derivative_state_samples.shape[0])
            ])
            h_val = dut.barrier_system.barrier_value(
                derivative_state_samples,
                dut.x_star,
                dut.c,
                inf_norm_term=dut.inf_norm_term)
            total_loss_expected += derivative_state_samples_weight * \
                torch.mean(torch.maximum(
                    -hdot - dut.epsilon * h_val,
                    torch.zeros_like(h_val, dtype=self.dtype)))
        self.assertAlmostEqual(total_loss.item(), total_loss_expected.item())

    def test_compute_sample_loss(self):
        c = 0.5
        epsilon = 0.5
        x_star = torch.tensor([0.5, 0.1], dtype=self.dtype)
        unsafe_region_cnstr1 = \
            gurobi_torch_mip.MixedIntegerConstraintsReturn()
        unsafe_region_cnstr1.Ain_input = torch.tensor([[1, 0]],
                                                      dtype=self.dtype)
        unsafe_region_cnstr1.rhs_in = torch.tensor([1], dtype=self.dtype)

        unsafe_state_samples = utils.uniform_sample_in_box(
            self.linear_system.x_lo, torch.tensor([1, 2], dtype=self.dtype),
            100)
        boundary_state_samples = utils.uniform_sample_on_box_boundary(
            self.linear_system.x_lo, self.linear_system.x_up, 200)
        derivative_state_samples = utils.uniform_sample_in_box(
            self.linear_system.x_lo, self.linear_system.x_up, 1000)
        inf_norm_term = barrier.InfNormTerm(
            torch.diag(2. /
                       (self.linear_system.x_up - self.linear_system.x_lo)),
            (self.linear_system.x_up + self.linear_system.x_lo) /
            (self.linear_system.x_up - self.linear_system.x_lo))

        for barrier_relu in (self.barrier_relu1, self.barrier_relu2,
                             self.barrier_relu3):
            dut = mut.TrainBarrier(
                control_barrier.ControlBarrier(self.linear_system,
                                               barrier_relu), x_star, c,
                unsafe_region_cnstr1,
                utils.box_boundary(self.linear_system.x_lo,
                                   self.linear_system.x_up), epsilon,
                inf_norm_term)
            self.compute_sample_loss_tester(dut, unsafe_state_samples,
                                            boundary_state_samples,
                                            derivative_state_samples, None, 2.,
                                            3.)
            self.compute_sample_loss_tester(dut, unsafe_state_samples,
                                            boundary_state_samples,
                                            derivative_state_samples, 0.5, 2.,
                                            None)

    def test_train_on_samples(self):
        c = 0.5
        epsilon = 0.5
        x_star = torch.tensor([0.5, 0.1], dtype=self.dtype)
        unsafe_region_cnstr = \
            gurobi_torch_mip.MixedIntegerConstraintsReturn()
        unsafe_region_cnstr.Ain_input = torch.tensor([[1, 0]],
                                                     dtype=self.dtype)
        unsafe_region_cnstr.rhs_in = torch.tensor([1], dtype=self.dtype)

        unsafe_state_samples = utils.uniform_sample_in_box(
            self.linear_system.x_lo, torch.tensor([1, 2], dtype=self.dtype),
            100)
        boundary_state_samples = utils.uniform_sample_on_box_boundary(
            self.linear_system.x_lo, self.linear_system.x_up, 200)
        deriv_state_samples = utils.uniform_sample_in_box(
            self.linear_system.x_lo, self.linear_system.x_up, 1000)
        inf_norm_term = barrier.InfNormTerm(
            torch.diag(2. /
                       (self.linear_system.x_up - self.linear_system.x_lo)),
            (self.linear_system.x_up + self.linear_system.x_lo) /
            (self.linear_system.x_up - self.linear_system.x_lo))

        dut = mut.TrainBarrier(
            control_barrier.ControlBarrier(self.linear_system,
                                           self.barrier_relu1), x_star, c,
            unsafe_region_cnstr,
            utils.box_boundary(self.linear_system.x_lo,
                               self.linear_system.x_up), epsilon,
            inf_norm_term)
        dut.derivative_state_samples_weight = 2.
        dut.boundary_state_samples_weight = 3.
        dut.unsafe_state_samples_weight = 4.
        dut.max_iterations = 3
        dut.train_on_samples(unsafe_state_samples, boundary_state_samples,
                             deriv_state_samples)

    def nominal_control_loss_tester(self, dut):
        loss = dut.nominal_controller_loss()
        x_samples = dut.nominal_control_option.sample_states

        u_samples = dut.nominal_control_option.controller.output(x_samples)
        hdot = dut.barrier_system.minimal_barrier_derivative_given_action(
            x_samples, u_samples, inf_norm_term=dut.inf_norm_term)
        h = dut.barrier_system.barrier_value(x_samples,
                                             dut.x_star,
                                             dut.c,
                                             inf_norm_term=dut.inf_norm_term)
        if dut.nominal_control_option.norm == "mean":
            loss_expected = dut.nominal_control_option.weight * torch.mean(
                torch.maximum(
                    -hdot - dut.epsilon * h +
                    dut.nominal_control_option.margin,
                    torch.zeros_like(hdot, dtype=self.dtype)))
        elif dut.nominal_control_option.norm == "max":
            loss_expected = dut.nominal_control_option.weight * torch.max(
                -hdot - dut.epsilon * h + dut.nominal_control_option.margin)

        self.assertAlmostEqual(loss.item(), loss_expected.item())

        all_parameters = list(
            dut.barrier_system.barrier_relu.parameters()) + list(
                dut.nominal_control_option.controller.parameters())
        for v in all_parameters:
            v.requires_grad = True
        loss.backward()
        grad = [v.grad.clone() for v in all_parameters if v.grad is not None]
        for v in all_parameters:
            v.grad.zero_()
        loss_expected.backward()
        grad_expected = [
            v.grad.clone() for v in all_parameters if v.grad is not None
        ]
        for (v1, v2) in zip(grad, grad_expected):
            np.testing.assert_allclose(v1.detach().numpy(),
                                       v2.detach().numpy())
        for v in all_parameters:
            v.grad.zero_()

    def test_nominal_controller_loss(self):
        c = 0.5
        epsilon = 1.5
        x_star = torch.tensor([0.5, 0.1], dtype=self.dtype)
        inf_norm_term = barrier.InfNormTerm.from_bounding_box(
            self.linear_system.x_lo, self.linear_system.x_up, 1.)
        controller_network = utils.setup_relu((2, 4, 3),
                                              params=None,
                                              negative_slope=0.1,
                                              bias=True,
                                              dtype=self.dtype)
        controller_network[0].weight.data = torch.tensor(
            [[3, 2], [-1, 3], [0, 1], [-1, -2]], dtype=self.dtype)
        controller_network[0].bias.data = torch.tensor([1, -2, 3, -1],
                                                       dtype=self.dtype)
        controller_network[2].weight.data = torch.tensor(
            [[3, -1, -2, 0], [1, 2, -1, -2], [0, 1, -1, 2]], dtype=self.dtype)
        controller_network[2].bias.data = torch.tensor([1, 3, 2],
                                                       dtype=self.dtype)
        controller = nominal_controller.NominalNNController(
            controller_network, None, None, self.linear_system.u_lo,
            self.linear_system.u_up)
        torch.manual_seed(0)
        x_samples = utils.uniform_sample_in_box(self.linear_system.x_lo,
                                                self.linear_system.x_up, 100)
        weight = 0.2
        margin = 0.5
        nominal_control_option1 = mut.NominalControlOption(
            controller,
            x_samples,
            weight,
            margin,
            norm="mean",
            nominal_control_loss_tol=0.5)
        dut1 = mut.TrainBarrier(
            control_barrier.ControlBarrier(self.linear_system,
                                           self.barrier_relu1), x_star, c,
            gurobi_torch_mip.MixedIntegerConstraintsReturn(),
            gurobi_torch_mip.MixedIntegerConstraintsReturn(), epsilon,
            inf_norm_term, nominal_control_option1)
        self.nominal_control_loss_tester(dut1)

        nominal_control_option2 = mut.NominalControlOption(
            controller,
            x_samples,
            weight,
            margin,
            norm="max",
            nominal_control_loss_tol=0.5)
        dut2 = mut.TrainBarrier(
            control_barrier.ControlBarrier(self.linear_system,
                                           self.barrier_relu1), x_star, c,
            gurobi_torch_mip.MixedIntegerConstraintsReturn(),
            gurobi_torch_mip.MixedIntegerConstraintsReturn(), epsilon,
            inf_norm_term, nominal_control_option2)
        self.nominal_control_loss_tester(dut2)


if __name__ == "__main__":
    unittest.main()
