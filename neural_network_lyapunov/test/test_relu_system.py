import gurobipy
import numpy as np
import unittest
import torch

import neural_network_lyapunov.relu_system as relu_system
import neural_network_lyapunov.gurobi_torch_mip as gurobi_torch_mip
import neural_network_lyapunov.utils as utils
import neural_network_lyapunov.mip_utils as mip_utils


def test_step_forward_batch(tester, dut, *args):
    x_next = dut.step_forward(*args)
    x = args[0]
    tester.assertEqual(x_next.shape, x.shape)
    for i in range(x.shape[0]):
        np.testing.assert_allclose(
            x_next[i].detach().numpy(),
            dut.step_forward(*[arg[i] for arg in args]).detach().numpy())


def setup_relu_dyn(dtype):
    # Construct a simple ReLU model with 3 hidden layers
    # params is the value of weights/bias after concatenation.
    # the network has the same number of outputs as inputs (2)
    relu1 = utils.setup_relu((2, 3, 4, 2),
                             params=None,
                             negative_slope=0.1,
                             bias=True,
                             dtype=dtype)
    relu1[0].weight.data = torch.tensor([[1, 2], [3, 4], [5, 6]], dtype=dtype)
    relu1[0].bias.data = torch.tensor([-11, 10, 5], dtype=dtype)
    relu1[2].weight.data = torch.tensor(
        [[-1, -0.5, 1.5], [2, 5, 6], [-2, -3, -4], [1.5, 4, 6]], dtype=dtype)
    relu1[2].bias.data = torch.tensor([-3, 2, 0.7, 1.5], dtype=dtype)
    relu1[4].weight.data = torch.tensor([[4, 5, 6, 7], [8, 7, 5.5, 4.5]],
                                        dtype=dtype)
    relu1[4].bias.data = torch.tensor([-9, 3], dtype=dtype)
    return relu1


def add_dynamics_constraint_tester(tester, dut):
    mip = gurobi_torch_mip.GurobiTorchMIP(dut.dtype)
    x_var = mip.addVars(dut.x_dim, lb=-gurobipy.GRB.INFINITY)
    x_next_var = mip.addVars(dut.x_dim, lb=-gurobipy.GRB.INFINITY)
    ret = dut.add_dynamics_constraint(mip,
                                      x_var,
                                      x_next_var,
                                      "",
                                      "",
                                      binary_var_type=gurobipy.GRB.BINARY)
    # Check the value for x_next_var
    torch.manual_seed(0)
    x_samples = utils.uniform_sample_in_box(dut.x_lo, dut.x_up, 100)
    mip.gurobi_model.setParam(gurobipy.GRB.Param.OutputFlag, False)
    for i in range(x_samples.shape[0]):
        for j in range(dut.x_dim):
            x_var[j].lb = x_samples[i, j].item()
            x_var[j].ub = x_samples[i, j].item()
        mip.gurobi_model.optimize()
        tester.assertEqual(mip.gurobi_model.status,
                           gurobipy.GRB.Status.OPTIMAL)
        x_next_val = np.array([v.x for v in x_next_var])
        np.testing.assert_allclose(
            x_next_val,
            dut.step_forward(x_samples[i]).detach().numpy())
        if dut.network_bound_propagate_method in (
                mip_utils.PropagateBoundsMethod.IA,
                mip_utils.PropagateBoundsMethod.IA_MIP):
            np.testing.assert_array_less(
                x_next_val,
                ret.x_next_ub_IA.detach().numpy() + 1E-6)
            np.testing.assert_array_less(ret.x_next_lb_IA.detach().numpy(),
                                         x_next_val + 1E-6)
        if dut.network_bound_propagate_method in (
                mip_utils.PropagateBoundsMethod.LP,
                mip_utils.PropagateBoundsMethod.MIP,
                mip_utils.PropagateBoundsMethod.IA_MIP):
            ret.x_next_bound_prog.gurobi_model.setParam(
                gurobipy.GRB.Param.OutputFlag, False)
            for i in range(dut.x_dim):
                ret.x_next_bound_prog.setObjective(
                    [torch.tensor([1], dtype=dut.dtype)],
                    [[ret.x_next_bound_var[i]]],
                    constant=0.,
                    sense=gurobipy.GRB.MAXIMIZE)
                ret.x_next_bound_prog.gurobi_model.optimize()
                tester.assertEqual(ret.x_next_bound_prog.gurobi_model.status,
                                   gurobipy.GRB.Status.OPTIMAL)
                tester.assertLessEqual(
                    x_next_val[i], ret.x_next_bound_prog.gurobi_model.ObjVal)
                # Check the lower bound
                ret.x_next_bound_prog.setObjective(
                    [torch.tensor([1], dtype=dut.dtype)],
                    [[ret.x_next_bound_var[i]]],
                    constant=0.,
                    sense=gurobipy.GRB.MINIMIZE)
                ret.x_next_bound_prog.gurobi_model.optimize()
                tester.assertEqual(ret.x_next_bound_prog.gurobi_model.status,
                                   gurobipy.GRB.Status.OPTIMAL)
                tester.assertGreaterEqual(
                    x_next_val[i], ret.x_next_bound_prog.gurobi_model.ObjVal)
    return ret


class TestAutonomousReluSystem(unittest.TestCase):
    def setUp(self):
        self.dtype = torch.float64
        self.relu_dyn = setup_relu_dyn(self.dtype)
        self.x_lo = torch.tensor([-1, -1], dtype=self.dtype)
        self.x_up = torch.tensor([-0.5, 2], dtype=self.dtype)
        self.system = relu_system.AutonomousReLUSystem(self.dtype, self.x_lo,
                                                       self.x_up,
                                                       self.relu_dyn)

    def test_relu_system_as_milp(self):
        dut = self.system
        mip_cnstr_return = dut.mixed_integer_constraints()
        self.assertIsNone(mip_cnstr_return.Aout_input)
        self.assertIsNone(mip_cnstr_return.Aout_binary)

        def check_transition(x0):
            milp = gurobi_torch_mip.GurobiTorchMILP(self.dtype)
            x = milp.addVars(dut.x_dim,
                             lb=-gurobipy.GRB.INFINITY,
                             vtype=gurobipy.GRB.CONTINUOUS,
                             name="x")
            s, gamma = milp.add_mixed_integer_linear_constraints(
                mip_cnstr_return, x, None, "s", "gamma", "relu_dynamics_ineq",
                "relu_dynamics_eq", "")
            for i in range(dut.x_dim):
                milp.addLConstr([torch.tensor([1.], dtype=self.dtype)],
                                [[x[i]]],
                                sense=gurobipy.GRB.EQUAL,
                                rhs=x0[i])
            milp.gurobi_model.setParam(gurobipy.GRB.Param.OutputFlag, 0)
            milp.gurobi_model.setParam(gurobipy.GRB.Param.DualReductions, 0)
            milp.gurobi_model.optimize()

            s_val = torch.tensor([si.X for si in s], dtype=self.dtype)
            x_next_val = mip_cnstr_return.Aout_slack @ s_val +\
                mip_cnstr_return.Cout
            np.testing.assert_array_almost_equal(
                x_next_val.detach().numpy(),
                dut.dynamics_relu(x0).detach().numpy(),
                decimal=5)

        torch.manual_seed(0)
        x0 = utils.uniform_sample_in_box(dut.x_lo, dut.x_up, 100)

        for i in range(x0.shape[0]):
            check_transition(x0[i])

    def test_step_forward(self):
        # Test a single x.
        x = torch.tensor([2., 3.], dtype=self.dtype)
        x_next = self.system.step_forward(x)
        x_next_expected = self.relu_dyn(x)
        np.testing.assert_allclose(x_next.detach().numpy(),
                                   x_next_expected.detach().numpy())
        # Test a batch of x
        x = torch.tensor([[2., 3.], [1., -2], [0., 4.]], dtype=self.dtype)
        test_step_forward_batch(self, self.system, x)

    def test_add_dynamics_constraint(self):
        dut = self.system
        ret = {}
        for network_bound_propagate_method in list(
                mip_utils.PropagateBoundsMethod):
            dut.network_bound_propagate_method = network_bound_propagate_method
            ret[network_bound_propagate_method] = \
                add_dynamics_constraint_tester(self, dut)

        # Check that using MIP generates tighter bounds than LP
        ret_LP = ret[mip_utils.PropagateBoundsMethod.LP]
        ret_MIP = ret[mip_utils.PropagateBoundsMethod.MIP]
        self.assertEqual(
            ret_LP.x_next_bound_prog.gurobi_model.getAttr(
                gurobipy.GRB.Attr.NumBinVars), 0)
        self.assertGreater(
            ret_MIP.x_next_bound_prog.gurobi_model.getAttr(
                gurobipy.GRB.Attr.NumBinVars), 0)
        for i in range(dut.x_dim):
            for sense in (gurobipy.GRB.MAXIMIZE, gurobipy.GRB.MINIMIZE):
                ret_LP.x_next_bound_prog.setObjective(
                    [torch.tensor([1], dtype=self.dtype)],
                    [[ret_LP.x_next_bound_var[i]]],
                    constant=0.,
                    sense=sense)
                ret_LP.x_next_bound_prog.gurobi_model.optimize()
                ret_MIP.x_next_bound_prog.setObjective(
                    [torch.tensor([1], dtype=self.dtype)],
                    [[ret_MIP.x_next_bound_var[i]]],
                    constant=0.,
                    sense=sense)
                ret_MIP.x_next_bound_prog.gurobi_model.optimize()
                if sense == gurobipy.GRB.MAXIMIZE:
                    self.assertLessEqual(
                        ret_MIP.x_next_bound_prog.gurobi_model.ObjVal,
                        ret_LP.x_next_bound_prog.gurobi_model.ObjVal)
                else:
                    self.assertLessEqual(
                        ret_LP.x_next_bound_prog.gurobi_model.ObjVal,
                        ret_MIP.x_next_bound_prog.gurobi_model.ObjVal + 1E-10)


def check_mixed_integer_constraints(tester, dut, x_val, u_val, autonomous):
    """
    Solve the MIP by constraining x[n] and u[n], the solution x[n+1]
    should match with calling step_forward(x[n], u[n]).
    """
    mip_cnstr_return = dut.mixed_integer_constraints()
    tester.assertIsNone(mip_cnstr_return.Aout_binary)

    milp = gurobi_torch_mip.GurobiTorchMILP(dut.dtype)
    x = milp.addVars(dut.x_dim,
                     lb=-gurobipy.GRB.INFINITY,
                     vtype=gurobipy.GRB.CONTINUOUS,
                     name="x")
    if not autonomous:
        u = milp.addVars(dut.u_dim,
                         lb=-gurobipy.GRB.INFINITY,
                         vtype=gurobipy.GRB.CONTINUOUS,
                         name="u")
        s, gamma = milp.add_mixed_integer_linear_constraints(
            mip_cnstr_return, x + u, None, "s", "gamma", "relu_dynamics_ineq",
            "relu_dynamics_eq", "")
    else:
        s, gamma = milp.add_mixed_integer_linear_constraints(
            mip_cnstr_return, x, None, "s", "gamma", "relu_dynamics_ineq",
            "relu_dynamics_eq", "")
    for i in range(dut.x_dim):
        milp.addLConstr([torch.tensor([1.], dtype=dut.dtype)], [[x[i]]],
                        sense=gurobipy.GRB.EQUAL,
                        rhs=x_val[i])
        if not autonomous:
            for i in range(dut.u_dim):
                milp.addLConstr([torch.tensor([1.], dtype=dut.dtype)],
                                [[u[i]]],
                                sense=gurobipy.GRB.EQUAL,
                                rhs=u_val[i])
        milp.gurobi_model.setParam(gurobipy.GRB.Param.OutputFlag, 0)
        milp.gurobi_model.setParam(gurobipy.GRB.Param.DualReductions, 0)
        milp.gurobi_model.optimize()

        s_val = torch.tensor([si.X for si in s], dtype=dut.dtype)
        x_next_val = mip_cnstr_return.Aout_slack @ s_val +\
            mip_cnstr_return.Cout
        nn_input = x_val if autonomous else torch.cat((x_val, u_val))
        if mip_cnstr_return.Aout_input is not None:
            x_next_val += mip_cnstr_return.Aout_input @ nn_input
    if autonomous:
        x_next_val_expected = dut.step_forward(x_val)
    else:
        x_next_val_expected = dut.step_forward(x_val, u_val)
    np.testing.assert_array_almost_equal(x_next_val.detach().numpy(),
                                         x_next_val_expected.detach().numpy(),
                                         decimal=5)


def check_add_dynamics_constraint(dut, x_val, u_val, atol=0, rtol=1E-7):
    mip = gurobi_torch_mip.GurobiTorchMIP(dut.dtype)
    assert (torch.all(x_val <= dut.x_up))
    assert (torch.all(x_val >= dut.x_lo))
    assert (torch.all(u_val <= dut.u_up))
    assert (torch.all(u_val >= dut.u_lo))
    x = mip.addVars(dut.x_dim,
                    lb=-gurobipy.GRB.INFINITY,
                    vtype=gurobipy.GRB.CONTINUOUS)
    u = mip.addVars(dut.u_dim,
                    lb=-gurobipy.GRB.INFINITY,
                    vtype=gurobipy.GRB.CONTINUOUS)
    x_next = mip.addVars(dut.x_dim,
                         lb=-gurobipy.GRB.INFINITY,
                         vtype=gurobipy.GRB.CONTINUOUS)
    dynamics_constraint_return = dut.add_dynamics_constraint(
        mip, x, x_next, u, "s", "gamma")
    forward_slack = dynamics_constraint_return.slack
    forward_binary = dynamics_constraint_return.binary
    assert (isinstance(forward_slack, list))
    assert (isinstance(forward_binary, list))
    mip.addMConstrs([torch.eye(dut.x_dim, dtype=dut.dtype)], [x],
                    b=x_val,
                    sense=gurobipy.GRB.EQUAL)
    mip.addMConstrs([torch.eye(dut.u_dim, dtype=dut.dtype)], [u],
                    b=u_val,
                    sense=gurobipy.GRB.EQUAL)
    mip.gurobi_model.setParam(gurobipy.GRB.Param.OutputFlag, False)
    mip.gurobi_model.optimize()
    x_next_expected = dut.step_forward(x_val, u_val)
    x_next_val = np.array([x_next[i].x for i in range(dut.x_dim)])
    np.testing.assert_allclose(x_next_val,
                               x_next_expected.detach().numpy(),
                               atol=atol,
                               rtol=rtol)


class TestReLUSystem(unittest.TestCase):
    def construct_relu_system_example(self):
        # Construct a ReLU system with nx = 2 and nu = 1
        self.dtype = torch.float64
        linear1 = torch.nn.Linear(3, 5, bias=True)
        linear1.weight.data = torch.tensor(
            [[0.1, 0.2, 0.3], [0.5, -0.2, 0.4], [0.1, 0.3, -1.2],
             [1.5, 0.3, 0.3], [0.2, 1.5, 0.1]],
            dtype=self.dtype)
        linear1.bias.data = torch.tensor([0.1, -1.2, 0.3, 0.2, -0.5],
                                         dtype=self.dtype)
        linear2 = torch.nn.Linear(5, 2, bias=True)
        linear2.weight.data = torch.tensor(
            [[0.1, -2.3, 1.5, 0.4, 0.2], [0.1, -1.2, -1.3, 0.3, 0.8]],
            dtype=self.dtype)
        linear2.bias.data = torch.tensor([0.2, -1.4], dtype=self.dtype)
        dynamics_relu = torch.nn.Sequential(linear1, torch.nn.LeakyReLU(0.1),
                                            linear2)

        x_lo = torch.tensor([-2, -2], dtype=self.dtype)
        x_up = torch.tensor([2, 2], dtype=self.dtype)
        u_lo = torch.tensor([-1], dtype=self.dtype)
        u_up = torch.tensor([1], dtype=self.dtype)
        dut = relu_system.ReLUSystem(self.dtype, x_lo, x_up, u_lo, u_up,
                                     dynamics_relu)
        return dut

    def test_mixed_integer_constraints(self):
        dut = self.construct_relu_system_example()
        self.assertEqual(dut.x_dim, 2)
        self.assertEqual(dut.u_dim, 1)

        result = dut.mixed_integer_constraints()
        self.assertIsNone(result.Aout_input)
        check_mixed_integer_constraints(self,
                                        dut,
                                        x_val=torch.tensor([0.2, 0.5],
                                                           dtype=dut.dtype),
                                        u_val=torch.tensor([0.1],
                                                           dtype=dut.dtype),
                                        autonomous=False)
        check_mixed_integer_constraints(self,
                                        dut,
                                        x_val=torch.tensor([1.2, 0.5],
                                                           dtype=dut.dtype),
                                        u_val=torch.tensor([0.1],
                                                           dtype=dut.dtype),
                                        autonomous=False)
        check_mixed_integer_constraints(self,
                                        dut,
                                        x_val=torch.tensor([-1.2, 0.3],
                                                           dtype=dut.dtype),
                                        u_val=torch.tensor([0.5],
                                                           dtype=dut.dtype),
                                        autonomous=False)

    def test_add_dynamics_constraint(self):
        dut = self.construct_relu_system_example()
        check_add_dynamics_constraint(dut,
                                      x_val=torch.tensor([0.2, 0.4],
                                                         dtype=dut.dtype),
                                      u_val=torch.tensor([0.1],
                                                         dtype=dut.dtype))
        check_add_dynamics_constraint(dut,
                                      x_val=torch.tensor([-0.2, 0.4],
                                                         dtype=dut.dtype),
                                      u_val=torch.tensor([-0.1],
                                                         dtype=dut.dtype))

    def test_possible_dx(self):
        # test a single x, u.
        dut = self.construct_relu_system_example()
        x = torch.tensor([0.1, 0.2], dtype=self.dtype)
        u = torch.tensor([0.5], dtype=self.dtype)
        x_next = dut.possible_dx(x, u)
        self.assertEqual(len(x_next), 1)
        np.testing.assert_allclose(x_next[0].detach().numpy(),
                                   dut.step_forward(x, u).detach().numpy())
        # test a batch of x, u.
        x = torch.tensor([[0.1, 0.2], [0.3, 0.4], [0.5, 0.]], dtype=self.dtype)
        u = torch.tensor([[0.5], [0.2], [-0.1]], dtype=self.dtype)
        x_next = dut.possible_dx(x, u)
        self.assertEqual(x_next[0].shape, (3, 2))
        for i in range(x.shape[0]):
            np.testing.assert_allclose(
                x_next[0][i].detach().numpy(),
                dut.step_forward(x[i], u[i]).detach().numpy())

    def test_step_forward(self):
        dut = self.construct_relu_system_example()
        # test a batch of x, u.
        x = torch.tensor([[0.1, 0.2], [0.3, 0.4], [0.5, 0.]], dtype=self.dtype)
        u = torch.tensor([[0.5], [0.2], [-0.1]], dtype=self.dtype)
        x_next = dut.step_forward(x, u)
        self.assertEqual(x_next.shape, (3, 2))
        for i in range(x.shape[0]):
            np.testing.assert_allclose(
                x_next[i].detach().numpy(),
                dut.step_forward(x[i], u[i]).detach().numpy())


class TestReLUSystemGivenEquilibrium(unittest.TestCase):
    def construct_relu_system_example(self):
        # Construct a ReLU system with nx = 2 and nu = 1
        self.dtype = torch.float64
        linear1 = torch.nn.Linear(3, 5, bias=True)
        linear1.weight.data = torch.tensor(
            [[0.1, 0.2, 0.3], [0.5, -0.2, 0.4], [0.1, 0.3, -1.2],
             [1.5, 0.3, 0.3], [0.2, 1.5, 0.1]],
            dtype=self.dtype)
        linear1.bias.data = torch.tensor([0.1, -1.2, 0.3, 0.2, -0.5],
                                         dtype=self.dtype)
        linear2 = torch.nn.Linear(5, 2, bias=True)
        linear2.weight.data = torch.tensor(
            [[0.1, -2.3, 1.5, 0.4, 0.2], [0.1, -1.2, -1.3, 0.3, 0.8]],
            dtype=self.dtype)
        linear2.bias.data = torch.tensor([0.2, -1.4], dtype=self.dtype)
        dynamics_relu = torch.nn.Sequential(linear1, torch.nn.LeakyReLU(0.1),
                                            linear2)

        x_lo = torch.tensor([-2, -2], dtype=self.dtype)
        x_up = torch.tensor([2, 2], dtype=self.dtype)
        u_lo = torch.tensor([-1], dtype=self.dtype)
        u_up = torch.tensor([1], dtype=self.dtype)
        x_equilibrium = torch.tensor([0.5, 0.3], dtype=self.dtype)
        u_equilibrium = torch.tensor([0.4], dtype=self.dtype)
        dut = relu_system.ReLUSystemGivenEquilibrium(self.dtype, x_lo, x_up,
                                                     u_lo, u_up, dynamics_relu,
                                                     x_equilibrium,
                                                     u_equilibrium)
        return dut

    def test_mixed_integer_constraints(self):
        dut = self.construct_relu_system_example()

        self.assertEqual(dut.x_dim, 2)
        self.assertEqual(dut.u_dim, 1)
        result = dut.mixed_integer_constraints()
        self.assertIsNone(result.Aout_input)
        check_mixed_integer_constraints(self,
                                        dut,
                                        x_val=torch.tensor([0.2, 0.5],
                                                           dtype=dut.dtype),
                                        u_val=torch.tensor([0.1],
                                                           dtype=dut.dtype),
                                        autonomous=False)
        check_mixed_integer_constraints(self,
                                        dut,
                                        x_val=torch.tensor([1.2, 0.5],
                                                           dtype=dut.dtype),
                                        u_val=torch.tensor([0.1],
                                                           dtype=dut.dtype),
                                        autonomous=False)
        check_mixed_integer_constraints(self,
                                        dut,
                                        x_val=torch.tensor([-1.2, 0.3],
                                                           dtype=dut.dtype),
                                        u_val=torch.tensor([0.5],
                                                           dtype=dut.dtype),
                                        autonomous=False)

    def test_add_dynamics_constraint(self):
        dut = self.construct_relu_system_example()
        check_add_dynamics_constraint(dut,
                                      x_val=torch.tensor([1.2, 0.5],
                                                         dtype=dut.dtype),
                                      u_val=torch.tensor([0.2],
                                                         dtype=dut.dtype))
        check_add_dynamics_constraint(dut,
                                      x_val=torch.tensor([-1.2, -0.2],
                                                         dtype=dut.dtype),
                                      u_val=torch.tensor([0.8],
                                                         dtype=dut.dtype))

    def test_step_forward(self):
        dut = self.construct_relu_system_example()

        # step_forward evaluated at the equilibrium should return the
        # equilibrium state.
        np.testing.assert_allclose(
            dut.step_forward(dut.x_equilibrium,
                             dut.u_equilibrium).detach().numpy(),
            dut.x_equilibrium.detach().numpy())

        def check(x, u):
            x_next_expected = dut.dynamics_relu(torch.cat((x, u))) -\
                dut.dynamics_relu(torch.cat((
                    dut.x_equilibrium, dut.u_equilibrium))) + dut.x_equilibrium
            x_next = dut.step_forward(x, u)
            np.testing.assert_allclose(x_next_expected.detach().numpy(),
                                       x_next.detach().numpy())

        check(torch.tensor([0.2, 0.6], dtype=dut.dtype),
              torch.tensor([0.5], dtype=dut.dtype))
        check(torch.tensor([-0.2, 0.6], dtype=dut.dtype),
              torch.tensor([0.9], dtype=dut.dtype))
        check(torch.tensor([-1.2, 1.6], dtype=dut.dtype),
              torch.tensor([-.2], dtype=dut.dtype))

        # Test a batch of x, u
        x = torch.tensor([[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]], dtype=dut.dtype)
        u = torch.tensor([[0.1], [-0.2], [0.5]], dtype=dut.dtype)
        test_step_forward_batch(self, dut, x, u)


class TestReLUSecondOrderSystemGivenEquilibrium(unittest.TestCase):
    def construct_relu_system_example(self):
        # Construct a ReLU system with nq = 2, nv = 2 and nu = 1
        self.dtype = torch.float64
        dynamics_relu = utils.setup_relu((5, 5, 2),
                                         params=None,
                                         negative_slope=0.01,
                                         bias=True,
                                         dtype=self.dtype)
        dynamics_relu[0].weight.data = torch.tensor(
            [[0.1, 0.2, 0.3, 0.4, -0.1], [0.5, -0.2, 0.4, 0.3, -0.2],
             [0.1, 0.3, -1.2, 1.2, -0.8], [1.5, 0.3, 0.3, -0.5, -2.1],
             [0.2, 1.5, 0.1, 0.9, 1.1]],
            dtype=self.dtype)
        dynamics_relu[0].bias.data = torch.tensor([0.1, -1.2, 0.3, 0.2, -0.5],
                                                  dtype=self.dtype)
        dynamics_relu[2].weight.data = torch.tensor(
            [[0.1, -2.3, 1.5, 0.4, 0.2], [0.1, -1.2, -1.3, 0.3, 0.8]],
            dtype=self.dtype)
        dynamics_relu[2].bias.data = torch.tensor([0.2, -1.4],
                                                  dtype=self.dtype)

        x_lo = torch.tensor([-2, -2, -5, -5], dtype=self.dtype)
        x_up = torch.tensor([2, 2, 5, 5], dtype=self.dtype)
        u_lo = torch.tensor([-5], dtype=self.dtype)
        u_up = torch.tensor([5], dtype=self.dtype)
        q_equilibrium = torch.tensor([0.5, 0.3], dtype=self.dtype)
        u_equilibrium = torch.tensor([0.4], dtype=self.dtype)
        dt = 0.01
        dut = relu_system.ReLUSecondOrderSystemGivenEquilibrium(
            self.dtype, x_lo, x_up, u_lo, u_up, dynamics_relu, q_equilibrium,
            u_equilibrium, dt)
        return dut

    def test_mixed_integer_constraints(self):
        dut = self.construct_relu_system_example()
        check_mixed_integer_constraints(self,
                                        dut,
                                        x_val=torch.tensor(
                                            [0.2, 0.5, 0.4, 0.3],
                                            dtype=dut.dtype),
                                        u_val=torch.tensor([0.1],
                                                           dtype=dut.dtype),
                                        autonomous=False)
        check_mixed_integer_constraints(self,
                                        dut,
                                        x_val=torch.tensor(
                                            [1.2, 0.5, 0.3, -0.4],
                                            dtype=dut.dtype),
                                        u_val=torch.tensor([0.1],
                                                           dtype=dut.dtype),
                                        autonomous=False)
        check_mixed_integer_constraints(self,
                                        dut,
                                        x_val=torch.tensor(
                                            [-1.2, 0.3, 0.8, -0.7],
                                            dtype=dut.dtype),
                                        u_val=torch.tensor([0.5],
                                                           dtype=dut.dtype),
                                        autonomous=False)

    def test_add_dynamics_constraint(self):
        dut = self.construct_relu_system_example()

        check_add_dynamics_constraint(dut,
                                      x_val=torch.tensor(
                                          [-1.2, 0.3, 0.8, -0.7],
                                          dtype=dut.dtype),
                                      u_val=torch.tensor([0.5],
                                                         dtype=dut.dtype))
        check_add_dynamics_constraint(dut,
                                      x_val=torch.tensor(
                                          [-0.2, -0.3, -0.8, -0.4],
                                          dtype=dut.dtype),
                                      u_val=torch.tensor([-0.5],
                                                         dtype=dut.dtype))

    def test_step_forward(self):
        dut = self.construct_relu_system_example()

        # step_forward evaluated at the equilibrium should return the
        # equilibrium state.
        np.testing.assert_allclose(
            dut.step_forward(dut.x_equilibrium,
                             dut.u_equilibrium).detach().numpy(),
            dut.x_equilibrium.detach().numpy())

        def check(x, u):
            with torch.no_grad():
                q = x[:dut.nq]
                v = x[dut.nq:]
                v_next = dut.dynamics_relu(torch.cat((x, u))) -\
                    dut.dynamics_relu(torch.cat((
                        dut.x_equilibrium, dut.u_equilibrium)))
                q_next = q + (v + v_next) / 2 * dut.dt
                x_next_expected = torch.cat((q_next, v_next))
                np.testing.assert_allclose(
                    dut.step_forward(x, u).detach().numpy(),
                    x_next_expected.detach().numpy())

        check(torch.tensor([0.5, 0.9, -0.1, -1.2], dtype=self.dtype),
              torch.tensor([1.5], dtype=self.dtype))
        check(torch.tensor([1.5, 2.9, -2.4, -0.7], dtype=self.dtype),
              torch.tensor([2.5], dtype=self.dtype))
        check(torch.tensor([0.2, -.9, -1.4, 0.1], dtype=self.dtype),
              torch.tensor([-2.1], dtype=self.dtype))

        # Test a batch of x, u
        x = torch.tensor([[0.5, 0.1, -0.3, -0.2], [0.2, 0.1, 0.4, 0.5]],
                         dtype=self.dtype)
        u = torch.tensor([[0.2], [-0.5]], dtype=self.dtype)
        test_step_forward_batch(self, dut, x, u)


class TestReLUSecondOrderResidueDynamicsGivenEquilibrium(unittest.TestCase):
    def construct_relu_system_example(self):
        # Construct a ReLU system with nq = 2, nv = 2 and nu = 1
        self.dtype = torch.float64
        dynamics_relu = utils.setup_relu((3, 5, 2),
                                         params=None,
                                         negative_slope=0.01,
                                         bias=True,
                                         dtype=self.dtype)
        dynamics_relu[0].weight.data = torch.tensor(
            [[0.1, 0.2, 0.3], [0.5, -0.2, 0.4], [0.1, 0.3, -1.2],
             [1.5, 0.3, 0.3], [0.2, 1.5, 0.1]],
            dtype=self.dtype)
        dynamics_relu[0].bias.data = torch.tensor([0.1, -1.2, 0.3, 0.2, -0.5],
                                                  dtype=self.dtype)
        dynamics_relu[2].weight.data = torch.tensor(
            [[0.1, -2.3, 1.5, 0.4, 0.2], [0.1, -1.2, -1.3, 0.3, 0.8]],
            dtype=self.dtype)
        dynamics_relu[2].bias.data = torch.tensor([0.2, -1.4],
                                                  dtype=self.dtype)

        x_lo = torch.tensor([-2, -2, -5, -5], dtype=self.dtype)
        x_up = torch.tensor([2, 2, 5, 5], dtype=self.dtype)
        u_lo = torch.tensor([-5], dtype=self.dtype)
        u_up = torch.tensor([5], dtype=self.dtype)
        q_equilibrium = torch.tensor([0.5, 0.3], dtype=self.dtype)
        u_equilibrium = torch.tensor([0.4], dtype=self.dtype)
        dt = 0.01
        dut = relu_system.ReLUSecondOrderResidueSystemGivenEquilibrium(
            self.dtype,
            x_lo,
            x_up,
            u_lo,
            u_up,
            dynamics_relu,
            q_equilibrium,
            u_equilibrium,
            dt,
            network_input_x_indices=[1, 3])
        return dut

    def test_step_forward(self):
        dut = self.construct_relu_system_example()

        # step_forward evaluated at the equilibrium should return the
        # equilibrium state.
        np.testing.assert_allclose(
            dut.step_forward(dut.x_equilibrium,
                             dut.u_equilibrium).detach().numpy(),
            dut.x_equilibrium.detach().numpy())

        def check(x, u):
            with torch.no_grad():
                q = x[:dut.nq]
                v = x[dut.nq:]
                v_next = v + dut.dynamics_relu(torch.cat((x[
                    dut._network_input_x_indices], u))) -\
                    dut.dynamics_relu(torch.cat((
                        dut.x_equilibrium[dut._network_input_x_indices],
                        dut.u_equilibrium)))
                q_next = q + (v + v_next) / 2 * dut.dt
                x_next_expected = torch.cat((q_next, v_next))
                np.testing.assert_allclose(
                    dut.step_forward(x, u).detach().numpy(),
                    x_next_expected.detach().numpy())

        check(torch.tensor([0.5, 0.9, -0.1, -1.2], dtype=self.dtype),
              torch.tensor([1.5], dtype=self.dtype))
        check(torch.tensor([1.5, 2.9, -2.4, -0.7], dtype=self.dtype),
              torch.tensor([2.5], dtype=self.dtype))
        check(torch.tensor([0.2, -.9, -1.4, 0.1], dtype=self.dtype),
              torch.tensor([-2.1], dtype=self.dtype))

        # Test a batch of x, u
        x = torch.tensor([[0.5, 0.1, -0.3, -0.2], [0.2, 0.1, 0.4, 0.5]],
                         dtype=self.dtype)
        u = torch.tensor([[0.2], [-0.5]], dtype=self.dtype)
        test_step_forward_batch(self, dut, x, u)

    def test_add_dynamics_constraint(self):
        dut = self.construct_relu_system_example()

        check_add_dynamics_constraint(dut,
                                      x_val=torch.tensor(
                                          [-1.2, 0.3, 0.8, -0.7],
                                          dtype=dut.dtype),
                                      u_val=torch.tensor([0.5],
                                                         dtype=dut.dtype))
        check_add_dynamics_constraint(dut,
                                      x_val=torch.tensor(
                                          [-0.2, -0.3, -0.8, -0.4],
                                          dtype=dut.dtype),
                                      u_val=torch.tensor([-0.5],
                                                         dtype=dut.dtype))


class TestAutonomousReLUSystemGivenEquilibrium(unittest.TestCase):
    def construct_relu_system_example(self, discrete_time_flag):
        # Construct a ReLU system with nx = 3
        self.dtype = torch.float64
        linear1 = torch.nn.Linear(3, 5, bias=True)
        linear1.weight.data = torch.tensor(
            [[0.1, 0.62, 0.3], [0.2, -0.2, 0.3], [0.1, 0.3, -1.2],
             [4.5, 0.7, 0.3], [0.1, 1.5, 0.1]],
            dtype=self.dtype)
        linear1.bias.data = torch.tensor([0.1, -4.2, 0.3, 0.2, -0.5],
                                         dtype=self.dtype)
        linear2 = torch.nn.Linear(5, 3, bias=True)
        linear2.weight.data = torch.tensor(
            [[0.1, -4.3, 1.5, 0.4, 0.2], [0.1, -1.2, -0.3, 0.3, 0.8],
             [0.3, -1.4, -0.1, 0.1, 1.1]],
            dtype=self.dtype)
        linear2.bias.data = torch.tensor([0.2, -1.4, -.5], dtype=self.dtype)
        dynamics_relu = torch.nn.Sequential(linear1, torch.nn.LeakyReLU(0.1),
                                            linear2)

        x_lo = torch.tensor([-2, -2, -2], dtype=self.dtype)
        x_up = torch.tensor([2, 2, 2], dtype=self.dtype)
        x_equilibrium = torch.tensor([-.1, 0.3, 0.5], dtype=self.dtype)
        dut = relu_system.AutonomousReLUSystemGivenEquilibrium(
            self.dtype, x_lo, x_up, dynamics_relu, x_equilibrium)
        return dut, x_equilibrium

    def test_mixed_integer_constraints(self):
        dut1, _ = self.construct_relu_system_example(True)
        dut2, _ = self.construct_relu_system_example(False)

        self.assertEqual(dut1.x_dim, 3)
        result = dut1.mixed_integer_constraints()
        self.assertIsNone(result.Aout_input)
        x_samples = utils.uniform_sample_in_box(dut1.x_lo, dut1.x_up, 10)
        for i in range(x_samples.shape[0]):
            check_mixed_integer_constraints(self,
                                            dut1,
                                            x_val=x_samples[i],
                                            u_val=None,
                                            autonomous=True)
            check_mixed_integer_constraints(self,
                                            dut2,
                                            x_val=x_samples[i],
                                            u_val=None,
                                            autonomous=True)

    def test_equilibrium(self):
        dut, x_equ = self.construct_relu_system_example(True)
        x_next = dut.step_forward(x_equ)

        np.testing.assert_array_almost_equal(x_next.detach().numpy(),
                                             x_equ.detach().numpy(),
                                             decimal=5)

    def step_forward_tester(self, dut):
        x_samples = utils.uniform_sample_in_box(dut.x_lo, dut.x_up, 10)
        x_next = dut.step_forward(x_samples)
        self.assertEqual(x_next.shape, x_samples.shape)
        for i in range(x_samples.shape[0]):
            x_next_i = dut.step_forward(x_samples[i])
            np.testing.assert_allclose(x_next[i].detach().numpy(),
                                       x_next_i.detach().numpy())
            x_next_expected = dut.dynamics_relu(
                x_samples[i]) - dut.dynamics_relu(
                    dut.x_equilibrium
                ) + dut.x_equilibrium if dut.discrete_time_flag else 0.
            np.testing.assert_allclose(x_next_i.detach().numpy(),
                                       x_next_expected.detach().numpy())

    def test_step_forward(self):
        dut1, x_equ1 = self.construct_relu_system_example(True)
        dut2, x_equ1 = self.construct_relu_system_example(True)
        self.step_forward_tester(dut1)
        self.step_forward_tester(dut2)

    def test_add_dynamics_constraint(self):
        dut1, _ = self.construct_relu_system_example(True)
        dut2, _ = self.construct_relu_system_example(False)

        for network_bound_propagate_method in list(
                mip_utils.PropagateBoundsMethod):
            dut1.network_bound_propagate_method = \
                network_bound_propagate_method
            add_dynamics_constraint_tester(self, dut1)
            dut2.network_bound_propagate_method = \
                network_bound_propagate_method
            add_dynamics_constraint_tester(self, dut2)


class TestAutonomousResidualReLUSystemGivenEquilibrium(unittest.TestCase):
    def construct_relu_system_example(self, discrete_time_flag):
        # Construct a ReLU system with nx = 3
        self.dtype = torch.float64
        linear1 = torch.nn.Linear(3, 5, bias=True)
        linear1.weight.data = torch.tensor(
            [[0.1, 0.62, 0.3], [0.2, -0.2, 0.3], [0.1, 0.3, -1.2],
             [4.5, 0.7, 0.3], [0.1, 1.5, 0.1]],
            dtype=self.dtype)
        linear1.bias.data = torch.tensor([0.1, -4.2, 0.3, 0.2, -0.5],
                                         dtype=self.dtype)
        linear2 = torch.nn.Linear(5, 3, bias=True)
        linear2.weight.data = torch.tensor(
            [[0.1, -4.3, 1.5, 0.4, 0.2], [0.1, -1.2, -0.3, 0.3, 0.8],
             [0.3, -1.4, -0.1, 0.1, 1.1]],
            dtype=self.dtype)
        linear2.bias.data = torch.tensor([0.2, -1.4, -.5], dtype=self.dtype)
        dynamics_relu = torch.nn.Sequential(linear1, torch.nn.LeakyReLU(0.1),
                                            linear2)

        x_lo = torch.tensor([-2, -2, -2], dtype=self.dtype)
        x_up = torch.tensor([2, 2, 2], dtype=self.dtype)
        x_equilibrium = torch.tensor([-.1, 0.3, 0.5], dtype=self.dtype)
        dut = relu_system.AutonomousResidualReLUSystemGivenEquilibrium(
            self.dtype, x_lo, x_up, dynamics_relu, x_equilibrium,
            discrete_time_flag)
        return dut, x_equilibrium

    def test_mixed_integer_constraints(self):
        dut1, _ = self.construct_relu_system_example(True)
        dut2, _ = self.construct_relu_system_example(False)

        self.assertEqual(dut1.x_dim, 3)

        torch.manual_seed(0)
        x_samples = utils.uniform_sample_in_box(dut1.x_lo, dut1.x_up, 10)
        for i in range(x_samples.shape[0]):

            check_mixed_integer_constraints(self,
                                            dut1,
                                            x_val=x_samples[i],
                                            u_val=None,
                                            autonomous=True)
            check_mixed_integer_constraints(self,
                                            dut2,
                                            x_val=x_samples[i],
                                            u_val=None,
                                            autonomous=True)

    def test_equilibrium(self):
        dut, x_equ = self.construct_relu_system_example(True)
        x_next = dut.step_forward(x_equ)

        np.testing.assert_array_almost_equal(x_next.detach().numpy(),
                                             x_equ.detach().numpy(),
                                             decimal=5)

    def step_forward_tester(self, dut):
        torch.manual_seed(0)
        x_samples = utils.uniform_sample_in_box(dut.x_lo, dut.x_up, 100)
        x_next = dut.step_forward(x_samples)
        self.assertEqual(x_samples.shape, x_next.shape)
        for i in range(x_samples.shape[0]):
            x_next_i = dut.step_forward(x_samples[i])
            np.testing.assert_allclose(x_next[i].detach().numpy(),
                                       x_next_i.detach().numpy())
            x_next_expected = dut.dynamics_relu(
                x_samples[i]) - dut.dynamics_relu(dut.x_equilibrium) + (
                    x_samples[i] if dut.discrete_time_flag else 0.)
            np.testing.assert_allclose(x_next_i.detach().numpy(),
                                       x_next_expected.detach().numpy())

    def test_step_forward(self):
        dut1, _ = self.construct_relu_system_example(True)
        dut2, _ = self.construct_relu_system_example(False)
        self.step_forward_tester(dut1)
        self.step_forward_tester(dut2)

    def test_add_dynamics_constraint(self):
        dut1, _ = self.construct_relu_system_example(True)
        dut2, _ = self.construct_relu_system_example(False)

        for network_bound_propagate_method in list(
                mip_utils.PropagateBoundsMethod):
            dut1.network_bound_propagate_method = \
                network_bound_propagate_method
            add_dynamics_constraint_tester(self, dut1)
            dut2.network_bound_propagate_method = \
                network_bound_propagate_method
            add_dynamics_constraint_tester(self, dut2)


if __name__ == "__main__":
    unittest.main()
