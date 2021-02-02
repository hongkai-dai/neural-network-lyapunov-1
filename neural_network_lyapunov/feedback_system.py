"""
For a given controller piecewise affine dynamical system x[n+1] = f(x[n],u[n]),
(represented either by a neural network with ReLU activation functions, or a
HybridLinearSystem), suppose the equilibrium of this system is x*, namely
x* = f(x*, u*)
we construct a piecewise affine controller
u[n] = ϕᵤ(x[n]) - ϕᵤ(x*) + u*
where ϕᵤ(x) is a neural network with ReLU activation functions. This feedback
system is still a piecewise affine system. We can represent the relationship
between x[n], x[n+1] and u[n] with mixed-integer linear constraints.
"""

import torch
import numpy as np

import gurobipy

import neural_network_lyapunov.hybrid_linear_system as hybrid_linear_system
import neural_network_lyapunov.relu_to_optimization as relu_to_optimization
import neural_network_lyapunov.gurobi_torch_mip as gurobi_torch_mip
import neural_network_lyapunov.utils as utils
import neural_network_lyapunov.mip_utils as mip_utils
import neural_network_lyapunov.compute_xhat as compute_xhat


class FeedbackSystem:
    """
    The forward model x[n+1] = f(x[n], u[n]) can be either a
    HybridLinearSystem class, or a ReLU system.
    The feedback controller is u[n] = ϕᵤ(x[n]) - ϕᵤ(x*), where ϕᵤ is another
    neural network with (leaky) ReLU activations.
    """
    def __init__(self,
                 forward_system,
                 controller_network,
                 x_equilibrium: torch.Tensor,
                 u_equilibrium: torch.Tensor,
                 u_lower_limit: np.ndarray,
                 u_upper_limit: np.ndarray,
                 *,
                 xhat_indices=None):
        """
        @param forward_system. The forward dynamical system representing
        x[n+1] = f(x[n], u[n]). This system must implements functions like
        add_dynamics_constraint(). Check ReLUSystemGivenEquilibrium in
        relu_system.py as a reference.
        @param controller_network The network ϕᵤ, where the control law is
        u[n] = ϕᵤ(x[n]) - ϕᵤ(x̂) + u*
        where x̂[i] = x*[i] if i is in xhat_indices, otherwise x̂[i]=x[i]. This
        means that we allow u[n] = u* when x[xhat_indices]=x*[xhat_indices].
        @param x_equilibrium The equilibrium state.
        @param u_equilibrium The control action at equilibrium.
        @param u_lower_limit The lower limit for the control u[n]. We will
        saturate the control if it is below u_lower_limit. Set to
        u_lower_limit[i] to -infinity if you don't want saturation for the i'th
        control.
        @param u_upper_limit The upper limit for the control u[n]. We will
        saturate the control if it is above u_upper_limit. Set to
        u_upper_limit[i] to infinity if you don't want saturation for the i'th
        control.
        @param xhat_indices x̂[i] = x*[i] if i is in xhat_indices, otherwise
        x̂[i]=x[i]. By default xhat_indices=None means x̂=x*.
        @note If a control has a lower limit, it has to also have an upper
        limit, and vice versa.
        """
        self.forward_system = forward_system
        self.x_dim = self.forward_system.x_dim
        self.x_lo_all = self.forward_system.x_lo_all
        self.x_up_all = self.forward_system.x_up_all
        self.dtype = self.forward_system.dtype
        if isinstance(controller_network, torch.nn.Sequential):
            assert (
                controller_network[0].in_features == self.forward_system.x_dim)
            assert (controller_network[-1].out_features ==
                    self.forward_system.u_dim)
        elif isinstance(controller_network, torch.nn.Linear):
            assert (
                controller_network.in_features == self.forward_system.x_dim)
            assert (
                controller_network.out_features == self.forward_system.u_dim)
        else:
            raise Exception("Unknown controller type.")
        self.controller_network = controller_network
        assert (x_equilibrium.shape == (self.forward_system.x_dim, ))
        assert (x_equilibrium.dtype == self.dtype)
        self.x_equilibrium = x_equilibrium
        assert (u_equilibrium.shape == (self.forward_system.u_dim, ))
        assert (u_equilibrium.dtype == self.dtype)
        self.u_equilibrium = u_equilibrium
        if isinstance(self.controller_network, torch.nn.Sequential):
            self.controller_relu_free_pattern = \
                relu_to_optimization.ReLUFreePattern(
                    self.controller_network, self.dtype)
        assert (isinstance(u_lower_limit, np.ndarray))
        assert (u_lower_limit.shape == (self.forward_system.u_dim, ))
        assert (isinstance(u_upper_limit, np.ndarray))
        assert (u_upper_limit.shape == (self.forward_system.u_dim, ))
        self.u_lower_limit = u_lower_limit
        self.u_upper_limit = u_upper_limit
        self.xhat_indices = xhat_indices
        self.controller_network_bound_propagate_method = \
            mip_utils.PropagateBoundsMethod.IA

    def _add_network_controller_mip_constraint(self, mip, x_var, u_var,
                                               controller_slack_var_name,
                                               controller_binary_var_name):
        assert (isinstance(self.controller_network, torch.nn.Sequential))
        controller_mip_cnstr, _, _, _, _, controller_network_output_lo,\
            controller_network_output_up = \
            self.controller_relu_free_pattern.output_constraint(
                torch.from_numpy(self.forward_system.x_lo_all),
                torch.from_numpy(self.forward_system.x_up_all),
                self.controller_network_bound_propagate_method)
        assert (controller_mip_cnstr.Aout_input is None)
        assert (controller_mip_cnstr.Aout_binary is None)
        controller_slack, controller_binary = \
            mip.add_mixed_integer_linear_constraints(
                controller_mip_cnstr, x_var, None,
                controller_slack_var_name, controller_binary_var_name,
                "controller_ineq", "controller_eq", "")
        u_pre_sat = mip.addVars(self.forward_system.u_dim,
                                lb=-gurobipy.GRB.INFINITY,
                                vtype=gurobipy.GRB.CONTINUOUS,
                                name="u_pre_sat")
        # Write ϕᵤ(x̂) = relu_xhat_coeff * relu_xhat_var + relu_xhat_constant
        if self.xhat_indices is None or self.xhat_indices == list(
                range(self.x_dim)):
            relu_xhat_coeff = []
            relu_xhat_var = []
            relu_xhat_constant = self.controller_network(self.x_equilibrium)
        else:
            relu_xhat_slack, relu_xhat_binary, relu_xhat_Aout,\
                relu_xhat_Cout, _, controller_relu_xhat_lo,\
                controller_relu_xhat_up\
                = compute_xhat._compute_network_at_xhat(
                    mip, x_var, self.x_equilibrium,
                    self.controller_relu_free_pattern, self.xhat_indices,
                    torch.from_numpy(self.forward_system.x_lo_all),
                    torch.from_numpy(self.forward_system.x_up_all))
            relu_xhat_coeff = [relu_xhat_Aout]
            relu_xhat_var = [relu_xhat_slack]
            relu_xhat_constant = relu_xhat_Cout.reshape((-1, ))
        # Add the input saturation constraint
        # u_pre_sat = ϕᵤ(x[n]) - ϕᵤ(x̂) + u*
        # and u[n] = saturation(u_pre_sat)
        # If we write
        # ϕᵤ(x̂) = relu_xhat_coeff * relu_xhat_var + relu_xhat_constant
        # Then the constraint is
        # Aout_slack * controller_slack -u_pre_sat
        # - relu_xhat_coeff * relu_xhat_var = relu_xhat_constant - u* -Cout
        if isinstance(self.controller_network, torch.nn.Sequential):
            mip.addMConstrs([
                controller_mip_cnstr.Aout_slack.reshape(
                    (self.forward_system.u_dim, len(controller_slack))),
                -torch.eye(self.forward_system.u_dim,
                           dtype=self.forward_system.dtype)
            ] + [-coeff for coeff in relu_xhat_coeff],
                            [controller_slack, u_pre_sat] + relu_xhat_var,
                            sense=gurobipy.GRB.EQUAL,
                            b=relu_xhat_constant - self.u_equilibrium -
                            controller_mip_cnstr.Cout,
                            name="controller_output")
        # Now compute the bounds of ϕᵤ(x̂)
        if self.xhat_indices is None or self.xhat_indices == list(
                range(self.x_dim)):
            controller_relu_xhat_lo = self.controller_network(
                self.x_equilibrium)
            controller_relu_xhat_up = controller_relu_xhat_lo

        u_pre_sat_lo = controller_network_output_lo -\
            controller_relu_xhat_up + self.u_equilibrium
        u_pre_sat_up = controller_network_output_up -\
            controller_relu_xhat_lo + self.u_equilibrium
        u_lower_bound, u_upper_bound = _add_input_saturation_constraint(
            mip, u_var, u_pre_sat, self.u_lower_limit, self.u_upper_limit,
            u_pre_sat_lo, u_pre_sat_up, self.dtype)
        return controller_slack, controller_binary, u_lower_bound,\
            u_upper_bound

    def _add_linear_controller_mip_constraint(self, mip, x_var, u_var):
        assert (isinstance(self.controller_network, torch.nn.Linear))
        controller_slack = []
        controller_binary = []
        u_pre_sat = mip.addVars(self.forward_system.u_dim,
                                lb=-gurobipy.GRB.INFINITY,
                                vtype=gurobipy.GRB.CONTINUOUS,
                                name="u_pre_sat")
        assert (self.xhat_indices is None
                or self.xhat_indices == list(range(self.x_dim)))

        network_at_x_equilibrium = self.controller_network(self.x_equilibrium)
        # Add the input saturation constraint
        # u_pre_sat = ϕᵤ(x[n]) - ϕᵤ(x*) + u*
        # and u[n] = saturation(u_pre_sat)
        # The constraint is
        # W * x[n] -u_pre_sat =ϕᵤ(x*) - u* - bias
        if self.controller_network.bias is not None:
            bias = self.controller_network.bias
        else:
            bias = torch.zeros((self.forward_system.u_dim, ), dtype=self.dtype)
        mip.addMConstrs([
            -torch.eye(self.forward_system.u_dim, dtype=self.dtype),
            self.controller_network.weight
        ], [u_pre_sat, x_var],
                        b=-self.u_equilibrium + network_at_x_equilibrium -
                        bias,
                        sense=gurobipy.GRB.EQUAL,
                        name="controller")
        controller_network_output_lo, controller_network_output_up =\
            mip_utils.propagate_bounds(
                self.controller_network,
                torch.from_numpy(self.forward_system.x_lo_all),
                torch.from_numpy(self.forward_system.x_up_all))
        u_pre_sat_lo = controller_network_output_lo -\
            network_at_x_equilibrium + self.u_equilibrium
        u_pre_sat_up = controller_network_output_up -\
            network_at_x_equilibrium + self.u_equilibrium
        u_lower_bound, u_upper_bound = _add_input_saturation_constraint(
            mip, u_var, u_pre_sat, self.u_lower_limit, self.u_upper_limit,
            u_pre_sat_lo, u_pre_sat_up, self.dtype)
        return controller_slack, controller_binary, u_lower_bound,\
            u_upper_bound

    def _add_controller_mip_constraint(self, mip, x_var, u_var,
                                       controller_slack_var_name,
                                       controller_binary_var_name):
        if isinstance(self.controller_network, torch.nn.Sequential):
            return self._add_network_controller_mip_constraint(
                mip, x_var, u_var, controller_slack_var_name,
                controller_binary_var_name)
        elif isinstance(self.controller_network, torch.nn.Linear):
            return self._add_linear_controller_mip_constraint(
                mip, x_var, u_var)

    def add_dynamics_mip_constraint(self, mip, x_var, x_next_var, u_var_name,
                                    forward_slack_var_name,
                                    forward_binary_var_name,
                                    controller_slack_var_name,
                                    controller_binary_var_name):
        assert (isinstance(mip, gurobi_torch_mip.GurobiTorchMIP))
        u = mip.addVars(self.forward_system.u_dim,
                        lb=-gurobipy.GRB.INFINITY,
                        vtype=gurobipy.GRB.CONTINUOUS,
                        name=u_var_name)
        # Now add the forward dynamics constraint
        forward_slack, forward_binary = \
            self.forward_system.add_dynamics_constraint(
                mip, x_var, x_next_var, u, forward_slack_var_name,
                forward_binary_var_name)

        controller_slack, controller_binary, u_lower_bound, u_upper_bound = \
            self._add_controller_mip_constraint(
                mip, x_var, u, controller_slack_var_name,
                controller_binary_var_name)

        return u, forward_slack, controller_slack, forward_binary,\
            controller_binary

    def compute_u(self, x):
        """
        The controller is defined as
        u[n] = ϕᵤ(x[n]) - ϕᵤ(x*) + u*
        """
        xhat = compute_xhat._get_xhat_val(x, self.x_equilibrium,
                                          self.xhat_indices)
        u_pre_sat = self.controller_network(x) - \
            self.controller_network(xhat) + self.u_equilibrium
        if len(x.shape) == 1:
            u = torch.max(
                torch.min(u_pre_sat, torch.from_numpy(self.u_upper_limit)),
                torch.from_numpy(self.u_lower_limit))
        else:
            # batch of x
            u = torch.max(
                torch.min(
                    u_pre_sat,
                    torch.from_numpy(self.u_upper_limit).reshape((1, -1))),
                torch.from_numpy(self.u_lower_limit).reshape((1, -1)))
        return u

    def possible_dx(self, x):
        u = self.compute_u(x)
        return self.forward_system.possible_dx(x, u)

    def step_forward(self, x):
        u = self.compute_u(x)
        if isinstance(self.forward_system,
                      hybrid_linear_system.HybridLinearSystem):
            return self.forward_system.step_forward(x, u)[0]
        else:
            return self.forward_system.step_forward(x, u)


def _add_input_saturation_constraint(mip, u_var, u_pre_sat,
                                     u_lower_limit: np.ndarray,
                                     u_upper_limit: np.ndarray,
                                     u_pre_sat_lo: torch.Tensor,
                                     u_pre_sat_up: torch.Tensor, dtype):
    """
    Add the MIP constraints of the saturation block. Also output the bounds of
    u after the saturation.
    """
    assert (np.all(u_lower_limit <= u_upper_limit))
    assert (torch.all(u_pre_sat_lo <= u_pre_sat_up))
    u_lower_bound = torch.min(
        torch.max(torch.from_numpy(u_lower_limit), u_pre_sat_lo),
        torch.from_numpy(u_upper_limit))
    u_upper_bound = torch.max(
        torch.min(torch.from_numpy(u_upper_limit), u_pre_sat_up),
        torch.from_numpy(u_lower_limit))
    for i in range(len(u_var)):
        if np.isinf(u_lower_limit[i]) and\
                np.isinf(u_upper_limit[i]):
            mip.addLConstr([torch.tensor([1, -1], dtype=dtype)],
                           [[u_var[i], u_pre_sat[i]]],
                           rhs=0.,
                           sense=gurobipy.GRB.EQUAL)
        else:
            assert (not np.isinf(u_lower_limit[i])
                    and not np.isinf(u_upper_limit[i]))
            utils.add_saturation_as_mixed_integer_constraint(
                mip, u_pre_sat[i], u_var[i], u_lower_limit[i],
                u_upper_limit[i], u_pre_sat_lo[i], u_pre_sat_up[i])
    assert (torch.all(u_lower_bound <= u_upper_bound))
    return u_lower_bound, u_upper_bound
