import neural_network_lyapunov.lyapunov as lyapunov
import neural_network_lyapunov.utils as utils
import neural_network_lyapunov.control_affine_system as control_affine_system
import neural_network_lyapunov.gurobi_torch_mip as gurobi_torch_mip
import torch
import gurobipy


class ControlAffineSystemConstraintReturn(lyapunov.SystemConstraintReturn):
    """
    The return type of add_system_constraint()
    """
    def __init__(self, slack, binary):
        super(ControlAffineSystemConstraintReturn,
              self).__init__(slack, binary)
        self.mip_cnstr_f = None
        self.mip_cnstr_G = None


class ControlLyapunov(lyapunov.LyapunovHybridLinearSystem):
    """
    Given a control affine system with dynamics
    ẋ = f(x) + G(x)u
    with input bounds u_lo <= u <= u_up
    The conditions for its control Lyapunov function V(x) is
    V(x) > 0
    minᵤ V̇ < 0
    where we can compute minᵤ V̇ as
    minᵤ V̇
    = ∂V/∂x*f(x) + minᵤ ∂V/∂x*G(x)*u
    = ∂V/∂x*f(x) + ∂V/∂x*G(x)*(u_lo + u_up)/2
        - |∂V/∂x*G(x) * diag((u_up - u_lo)/2)|₁
    where |•|₁ denotes the 1-norm of a vector.

    The Lyapunov function V(x) is formulated as
    V(x) = ϕ(x) − ϕ(x*) + λ|R(x−x*)|₁
    where ϕ is a neural network with (leaky) ReLU units.

    We will check if minᵤ V̇ < 0 is satisfied by solving a mixed-integer
    program with x being the decision variable.
    """
    def __init__(self,
                 system: control_affine_system.ControlPiecewiseAffineSystem,
                 lyapunov_relu):
        """
        Args:
          system: A control-affine system.
          lyapunov_relu: The neural network ϕ which defines the Lyapunov
          function.
        """
        assert (isinstance(system,
                           control_affine_system.ControlPiecewiseAffineSystem))
        super(ControlLyapunov, self).__init__(system, lyapunov_relu)

    def lyapunov_derivative(self, x, x_equilibrium, V_lambda, epsilon, *, R):
        """
        Compute minᵤ V̇ + ε*V
        subject to u_lo <= u <= u_up
        Note that ∂V/∂x = ∂ϕ/∂x + λ ∑ᵢ sign(R[i, :](x−x*))R[i, :]
        Args:
          x_equilibrium: x* in the documentation.
          V_lambda: λ in the documentation.
          epsilon: ε in the documentation.
          R: A full column rank matrix. Use the identity matrix if R=None.
        """
        assert (isinstance(x, torch.Tensor))
        assert (x.shape == (self.system.x_dim, ))

        R = lyapunov._get_R(R, self.system.x_dim, x_equilibrium.device)

        # First compute ∂ϕ/∂x
        dphi_dx = utils.relu_network_gradient(self.lyapunov_relu, x).squeeze(1)

        # Now compute the gradient of λ|R(x−x*)|₁
        dl1_dx = V_lambda * utils.l1_gradient(R @ (x - x_equilibrium)) @ R

        # We compute the sum of each possible dphi_dX and dl1_dx
        dVdx = dphi_dx.repeat((dl1_dx.shape[0], 1)) + dl1_dx.repeat(
            (1, dphi_dx.shape[0])).view(
                (dphi_dx.shape[0] * dl1_dx.shape[0], self.system.x_dim))

        # minᵤ V̇
        # = ∂V/∂x*f(x) + ∂V/∂x*G(x)*(u_lo + u_up)/2
        #     - |∂V/∂x*G(x) * diag((u_up - u_lo)/2)|₁
        G = self.system.G(x)
        Vdot = dVdx @ self.system.f(x) + dVdx @ G @ (
            (self.system.u_lo + self.system.u_up) / 2) - torch.norm(
                (dVdx @ G) *
                ((self.system.u_up - self.system.u_lo) / 2).repeat(
                    (dVdx.shape[0], 1)),
                p=1,
                dim=1)
        V = self.lyapunov_value(x, x_equilibrium, V_lambda, R=R)
        return torch.min(Vdot.squeeze()) + epsilon * V

    def add_system_constraint(
        self,
        milp: gurobi_torch_mip.GurobiTorchMIP,
        x: list,
        f: list,
        G: list,
        *,
        binary_var_type=gurobipy.GRB.BINARY
    ) -> ControlAffineSystemConstraintReturn:
        """
        Add the (mixed-integer linear) constraints of f(x) and G(x).

        Args:
          G: G is a 2D list. len(G) = u_dim. len(G[i]) = x_dim
        """
        assert (len(f) == self.system.x_dim)
        assert (len(G) == self.system.u_dim)
        # Add constraint that x_lo <= x <= x_up
        milp.addMConstrs(
            [torch.eye(self.system.x_dim, dtype=self.system.dtype)], [x],
            gurobipy.GRB.LESS_EQUAL,
            self.system.x_up,
            name="x_up")
        milp.addMConstrs(
            [torch.eye(self.system.x_dim, dtype=self.system.dtype)], [x],
            gurobipy.GRB.GREATER_EQUAL,
            self.system.x_lo,
            name="x_lo")
        # Set the bounds of x
        for i in range(self.system.x_dim):
            if x[i].lb < self.system.x_lo[i].item():
                x[i].lb = self.system.x_lo[i].item()
            if x[i].ub > self.system.x_up[i].item():
                x[i].ub = self.system.x_up[i].item()
        mip_cnstr_f, mip_cnstr_G = self.system.mixed_integer_constraints()
        slack, binary = milp.add_mixed_integer_linear_constraints(
            mip_cnstr_f, x, f, "slack_f", "binary_f", "f_ineq", "f_eq",
            "f_output", binary_var_type)
        for i in range(self.system.u_dim):
            slack_Gi, binary_Gi = milp.add_mixed_integer_linear_constraints(
                mip_cnstr_G[i], x, G[i], f"slack_G[{i}]", f"binary_G[{i}]",
                "G[{i}]_ineq", "G[{i}]_eq", "G[{i}]_out", binary_var_type)
            slack.extend(slack_Gi)
            binary.extend(binary_Gi)
        ret = ControlAffineSystemConstraintReturn(slack, binary)
        ret.mip_cnstr_f = mip_cnstr_f
        ret.mip_cnstr_G = mip_cnstr_G
        return ret
