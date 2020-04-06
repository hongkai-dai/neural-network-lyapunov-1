import torch
import numpy as np


class SamplesBuffer:
    def __init__(self, samples_dim, labels_dim, dtype, max_size=None):
        self.samples_dim = samples_dim
        self.dtype = dtype
        self.max_size = max_size
        self.x_samples = torch.Tensor(0, samples_dim).type(dtype)
        self.v_labels = torch.Tensor(0, labels_dim).type(dtype)
        self.current_sample = 0

    def add_samples(self, new_x_samples, new_v_labels):
        """
        Add samples to the buffer
        @param new_x_samples Tensor n X samples_dim
        @param new_v_labels Tensor n X labels_dim
        """
        assert(new_x_samples.shape[0] == new_v_labels.shape[0])
        if self.max_size is not None:
            assert(new_x_samples.shape[0] <= self.max_size)
        num_new_samples = new_x_samples.shape[0]
        if (self.max_size is not None and
                self.num_samples + num_new_samples > self.max_size):
            num_extra = self.max_size - num_new_samples
            self.x_samples = self.x_samples[num_extra:, :]
            self.v_labels = self.v_labels[num_extra:, :]
        self.x_samples = torch.cat((self.x_samples, new_x_samples), axis=0)
        self.v_labels = torch.cat((self.v_labels, new_v_labels), axis=0)

    def get_random_sample_indices(self, num_indices):
        """
        @return random samples indices that can be used to recover random
        samples
        """
        indices = np.random.choice(self.num_samples,
                                   min(self.num_samples, num_indices),
                                   replace=False)
        return indices

    def get_next_sample_indices(self, num_indices):
        indices = np.arange(self.current_sample, self.current_sample + num_indices)
        indices %= self.num_samples
        self.current_sample = indices[-1] + 1
        return indices

    def get_samples_from_indices(self, indices):
        """
        @return tuple of tensor of the (samples, labels) corresponding to
        indices
        """
        return(self.x_samples[indices, :], self.v_labels[indices, :])

    def get_random_samples(self, num_rand_samples):
        """
        @return a tuple with (random samples, random labels)
        """
        indices = self.get_random_sample_indices(num_rand_samples)
        return self.get_samples_from_indices(indices)

    def get_next_samples(self, num_next_samples):
        """
        @return a tuple with (next samples, next labels)
        """
        indices = self.get_next_sample_indices(num_next_samples)
        return self.get_samples_from_indices(indices)

    @property
    def num_samples(self):
        """
        @return number of samples currently in the buffer
        """
        return self.x_samples.shape[0]
