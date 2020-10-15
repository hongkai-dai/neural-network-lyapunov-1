import torch
import numpy as np
import pybullet as pb
import matplotlib.pyplot as plt
from torch.utils.data import TensorDataset, DataLoader


def show_sample(X_sample, X_next_sample=None, clamp=False):
    """
    Plots a sample (in image space) generated by the PybulletSampleGenerator
    @arg X_sample tensor of dim [num_channels, width, height]
    @arg X_next_sample tensor of dim [num_channels, width, height]
    num_channels depends on the type of sample.
    1 channel: grayscale with only one snapshot
    2 channels: grayscale with 2 snapshots (to capture velocity)
    3 channels: rgb with 1 snapshot
    6 channels: rgb with 2 snapshots
    """
    if clamp:
        X_sample = torch.clamp(X_sample, 0, 1)
        if X_next_sample is not None:
            X_next_sample = torch.clamp(X_next_sample)
    if X_sample.shape[0] == 6:
        num_channels = 3
        cmap = None
    elif X_sample.shape[0] == 3:
        num_channels = 3
        cmap = None
    elif X_sample.shape[0] == 2:
        num_channels = 1
        cmap = 'gray'
    elif X_sample.shape[0] == 1:
        num_channels = 1
        cmap = 'gray'
    else:
        raise(NotImplementedError)
    fig = plt.figure(figsize=(10, 10))
    if X_next_sample is not None:
        fig.add_subplot(1, 3, 1)
        plt.imshow(X_sample[:num_channels, :, :].to(
            'cpu').detach().numpy().transpose(1, 2, 0),
            cmap=cmap, vmin=0, vmax=1)
        fig.add_subplot(1, 3, 2)
        plt.imshow(X_sample[num_channels:, :, :].to(
            'cpu').detach().numpy().transpose(1, 2, 0),
            cmap=cmap, vmin=0, vmax=1)
        fig.add_subplot(1, 3, 3)
        plt.imshow(X_next_sample[:num_channels, :, :].to(
            'cpu').detach().numpy().transpose(1, 2, 0),
            cmap=cmap, vmin=0, vmax=1)
    else:
        fig.add_subplot(1, 2, 1)
        plt.imshow(X_sample[:num_channels, :, :].to(
            'cpu').detach().numpy().transpose(1, 2, 0),
            cmap=cmap, vmin=0, vmax=1)
        if X_sample.shape[0] == 2 or X_sample.shape[0] == 6:
            fig.add_subplot(1, 2, 2)
            plt.imshow(X_sample[num_channels:, :, :].to(
                'cpu').detach().numpy().transpose(1, 2, 0),
                cmap=cmap, vmin=0, vmax=1)
    plt.show()


def add_noise(x_data, noise_std_percent):
    """
    Adds normal noise to a dataset
    @param noise_std_percent tensor with standard deviation of the noise,
    as percent of the mean of the magnitude for that dimension
    @return x_data_, same as x_data but with noise added to it
    """
    assert(isinstance(x_data, torch.Tensor))
    x_data_ = torch.clone(x_data)
    noise_std = noise_std_percent * torch.mean(torch.abs(x_data), dim=0)
    eps = torch.randn(x_data.shape, dtype=x_data.dtype)
    x_data_ += eps * noise_std
    return x_data_


def get_dataloaders(x_data, x_next_data, batch_size, validation_ratio):
    """
    generates dataloaders given datasets as tensors
    @param x_data, tensor of input data to the model
    [num_sample,2*num_channels,state size or width, nothing or height]
    @param x_next_data, tensor of output data to the model
    [num_sample,num_channels,state size or width, nothing or height]
    @param batch_size, int
    @param validation_ratio, float proportion of the data to include in the
    validation dataloader instead of the training dataloader
    @return torch DataLoaders, training dataloader and validation one
    """
    x_dataset = TensorDataset(x_data, x_next_data)
    train_size = int((1. - validation_ratio) * len(x_dataset))
    val_size = len(x_dataset) - train_size
    train_dataset, validation_dataset = torch.utils.data.random_split(
        x_dataset, [train_size, val_size])
    train_dataloader = DataLoader(
        train_dataset,
        batch_size=batch_size,
        shuffle=True
    )
    validation_dataloader = DataLoader(
        validation_dataset,
        batch_size=batch_size,
        shuffle=True
    )
    return train_dataloader, validation_dataloader


class PybulletSampleGenerator:
    def __init__(self, load_world_cb, joint_space,
                 image_width=80, image_height=80, grayscale=False,
                 camera_eye_position=[0, -3, 0],
                 camera_target_position=[0, 0, 0],
                 camera_up_vector=[0, 0, 1],
                 gui=False,
                 dtype=torch.float64):
        """
        Generates state transitions using pybullet. The transitions are
        returned both in image and state space
        @param load_world_cb a function that can be called with a single
        argument, the pybullet pysics client (pb) and sets up the environment
        @param joint_space, boolean set to true if the state of the robot
        is in joint space. If false, the state will be the position and
        orientation of the base of the robot, as well as the linear and angular
        velocities (x,y,z,r,p,y,x_dot,y_dot,z_dot,w_x,w_y,w_z)
        @param image_width, image_height: int number of pixels in the generated
        images
        @param grayscale: boolean, use grayscale or rgb
        @param camera_...: position and target of the virtual camera
        @param gui: boolean to show the gui or not
        """
        self.dtype = dtype
        if gui:
            self.physics_client = pb.connect(pb.GUI)
        else:
            self.physics_client = pb.connect(pb.DIRECT)
        pb.setGravity(0, 0, -9.8)
        self.timestep = 1./240.
        pb.setTimeStep(self.timestep)
        pb.setPhysicsEngineParameter(enableFileCaching=0)
        self.grayscale = grayscale
        self.grayscale_weight = [.2989, .5870, .1140]
        if self.grayscale:
            self.num_channels = 1
        else:
            self.num_channels = 3
        self.image_width = image_width
        self.image_height = image_height
        self.view_matrix = pb.computeViewMatrix(
            cameraEyePosition=camera_eye_position,
            cameraTargetPosition=camera_target_position,
            cameraUpVector=camera_up_vector)
        self.projection_matrix = pb.computeProjectionMatrixFOV(
            fov=45.0,
            aspect=1.0,
            nearVal=0.1,
            farVal=3.1)
        self.robot_id = load_world_cb(pb)
        self.joint_space = joint_space
        if self.joint_space:
            self.num_joints = pb.getNumJoints(self.robot_id)
            self.x_dim = 2 * self.num_joints
            for i in range(self.num_joints):
                pb.setJointMotorControl2(self.robot_id, i, pb.VELOCITY_CONTROL,
                                         force=0)
        else:
            self.x_dim = 12
        # runs the sim for .5 seconds to resolve self collisions
        for i in range(int(.5/self.timestep)):
            pb.stepSimulation()
        self.state_id = pb.saveState()

    def __del__(self):
        pb.disconnect(self.physics_client)

    def generate_sample(self, x0, dt):
        """
        generate a single transition from state x0. A first image is taken
        at time t=0, then another one at t=.5*dt. These two images are the
        initial state in image space. Then a last image is taken at t=dt.
        In state space, the first state is taken at t=0, the next state is
        taken at t=dt.
        @param x0, tensor of the initial state (either position/orientation
        or joint states)
        @param dt float time length between initial and final sample
        @return X, tensor corresponding to x0 in image space
        @return X_next, tensor corresponding to x0 after dt in image space
        @return x_next, tensor corresponding to x0 after dt in state space
        """
        assert(isinstance(x0, torch.Tensor))
        assert(len(x0) == self.x_dim)
        pb.restoreState(self.state_id)
        num_step = int(dt*(.5/self.timestep))
        X = np.zeros((6, self.image_width, self.image_height), dtype=np.uint8)
        X_next = np.zeros((3, self.image_width, self.image_height),
                          dtype=np.uint8)
        if self.joint_space:
            q0 = x0[:self.num_joints]
            v0 = x0[self.num_joints:self.x_dim]
            for i in range(len(q0)):
                pb.resetJointState(self.robot_id, i, q0[i], v0[i])
        else:
            pos0 = x0[:3]
            orn0 = pb.getQuaternionFromEuler(x0[3:6])
            vel0 = x0[6:9]
            w0 = x0[9:12]
            pb.resetBasePositionAndOrientation(self.robot_id, pos0, orn0)
            pb.resetBaseVelocity(self.robot_id, vel0, w0)
        width0, height0, rgb0, depth0, seg0 = pb.getCameraImage(
            width=self.image_width,
            height=self.image_height,
            viewMatrix=self.view_matrix,
            projectionMatrix=self.projection_matrix)
        for k in range(num_step):
            pb.stepSimulation()
        width1, height1, rgb1, depth1, seg1 = pb.getCameraImage(
            width=self.image_width,
            height=self.image_height,
            viewMatrix=self.view_matrix,
            projectionMatrix=self.projection_matrix)
        for k in range(num_step):
            pb.stepSimulation()
        if self.joint_space:
            state = pb.getJointStates(self.robot_id, list(range(len(q0))))
            q1 = state[0][:len(q0)]
            v1 = state[0][len(q0):len(q0)+len(v0)]
            q1 = torch.tensor(q1, dtype=self.dtype)
            v1 = torch.tensor(v1, dtype=self.dtype)
            x1 = torch.cat((q1, v1))
        else:
            pos1, orn1_quat = pb.getBasePositionAndOrientation(self.robot_id)
            orn1 = pb.getEulerFromQuaternion(orn1_quat)
            vel1, w1 = pb.getBaseVelocity(self.robot_id)
            pos1 = torch.tensor(pos1, dtype=self.dtype)
            orn1 = torch.tensor(orn1, dtype=self.dtype)
            vel1 = torch.tensor(vel1, dtype=self.dtype)
            w1 = torch.tensor(w1, dtype=self.dtype)
            x1 = torch.cat((pos1, orn1, vel1, w1))
        width2, height2, rgb2, depth2, seg2 = pb.getCameraImage(
            width=self.image_width,
            height=self.image_height,
            viewMatrix=self.view_matrix,
            projectionMatrix=self.projection_matrix)
        X[:3, :, :] = rgb0[:, :, :3].transpose(2, 0, 1)
        X[3:, :, :] = rgb1[:, :, :3].transpose(2, 0, 1)
        X_next[:3, :, :] = rgb2[:, :, :3].transpose(2, 0, 1)
        X = torch.tensor(X, dtype=torch.float64)
        X_next = torch.tensor(X_next, dtype=torch.float64)
        if self.grayscale:
            X_gray = torch.zeros(2, X.shape[1], X.shape[2],
                                 dtype=torch.float64)
            X_gray[0, :] = self.grayscale_weight[0] * X[0, :, :] +\
                self.grayscale_weight[1] * X[1, :, :] +\
                self.grayscale_weight[2] * X[2, :, :]
            X_gray[1, :] = self.grayscale_weight[0] * X[3, :, :] +\
                self.grayscale_weight[1] * X[4, :, :] +\
                self.grayscale_weight[2] * X[5, :, :]
            X_next_gray = torch.zeros(1, X_next.shape[1], X_next.shape[2],
                                      dtype=torch.float64)
            X_next_gray[0, :, :] = \
                self.grayscale_weight[0] * X_next[0, :, :] +\
                self.grayscale_weight[1] * X_next[1, :, :] +\
                self.grayscale_weight[2] * X_next[2, :, :]
            X = X_gray
            X_next = X_next_gray
        X /= 255.
        X_next /= 255.
        X = torch.clamp(X, 0., 1.)
        X_next = torch.clamp(X_next, 0., 1.)
        X = X.type(self.dtype)
        X_next = X_next.type(self.dtype)
        return X, X_next, x1

    def generate_rollout(self, x0, dt, N):
        """
        generates a rollouts of the system using pybullet
        @return X_data, tensor [N+2,num_channels,width,height]
        @return x_data, tensor [N+1,state dim]
        """
        X_data = torch.empty((N+2, self.num_channels,
                             self.image_width, self.image_height),
                             dtype=self.dtype)
        x_data = torch.empty((N+1, self.x_dim), dtype=self.dtype)
        X, _, _ = self.generate_sample(x0, dt)
        X_data[0, :] = X[:self.num_channels, :]
        X_data[1, :] = X[self.num_channels:, :]
        x_data[0, :] = x0
        for n in range(N):
            _, X_next, x_next = self.generate_sample(x_data[n], dt)
            X_data[n+2] = X_next
            x_data[n+1] = x_next
        return X_data, x_data

    def generate_dataset(self, x_lo, x_up, dt, N, num_rollouts):
        """
        generates a dataset using pybullet
        @param x_lo, x_up, bounding box on the initial states of the system
        @param dt float time step size
        @param N int length each rollout
        @param num_rollouts int number of rollouts
        """
        assert(N >= 1)
        X_data = torch.empty((num_rollouts * N, 2 * self.num_channels,
                             self.image_width, self.image_height),
                             dtype=self.dtype)
        X_next_data = torch.empty((num_rollouts * N, self.num_channels,
                                  self.image_width, self.image_height),
                                  dtype=self.dtype)
        x_data = torch.empty((num_rollouts * N, self.x_dim), dtype=self.dtype)
        x_next_data = torch.empty((num_rollouts * N, self.x_dim),
                                  dtype=self.dtype)
        for i in range(num_rollouts):
            x0 = torch.rand(self.x_dim) * (x_up - x_lo) + x_lo
            X_data_rollout, x_data_rollout = self.generate_rollout(x0, dt, N)
            for n in range(N):
                X_data[i * N + n, :self.num_channels, :] = X_data_rollout[n, :]
                X_data[i * N + n, self.num_channels:, :] = X_data_rollout[
                    n+1, :]
                X_next_data[i * N + n, :] = X_data_rollout[n+2, :]
                x_data[i * N + n, :] = x_data_rollout[n, :]
                x_next_data[i * N + n, :] = x_data_rollout[n+1, :]
        return x_data, x_next_data, X_data, X_next_data

    def data_to_rollouts(self, x_data, dt, N):
        """
        Takes a bunch of initial states and generates a list of rollouts,
        usefull to validate the way error grows along rollouts
        @param x_data, tensor [number of initial states, state_dim]
        @param dt, float time step size
        @param N, int number of time step taken
        """
        X_rollouts = []
        x_rollouts = []
        for k in range(x_data.shape[0]):
            rX, rx = self.generate_rollout(x_data[k, :], dt, N)
            X_rollouts.append(rX)
            x_rollouts.append(rx)
        return X_rollouts, x_rollouts
