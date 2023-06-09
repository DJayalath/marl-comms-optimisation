"""
JO-IPPO

Jointly Observable IPPO (JO-IPPO) provides the joint observations of all agents as input in addition to the agent
observation.

Configurations:
- Joint observations encoded by SAE to latent_dim (pre-trained/policy losses/reconstruction losses)
- Joint observations encoded by MLP to latent_dim (pre-trained/policy losses/reconstruction losses)
- Joint observations not encoded
"""

from ray.rllib.models.torch.torch_modelv2 import TorchModelV2
from sae.model import AutoEncoder as SAE
import torch
from scenario_config import SCENARIO_CONFIG

class PolicyJOIPPO(TorchModelV2, torch.nn.Module):

    def __init__(self, observation_space, action_space, num_outputs, model_config, name, *args, **kwargs):

        # Call super class constructors
        TorchModelV2.__init__(self, observation_space, action_space, num_outputs, model_config, name)
        torch.nn.Module.__init__(self)

        device = "cuda" if torch.cuda.is_available() else "cpu"

        # Process keyword arguments
        scenario_name = kwargs.get("scenario_name")
        cwd = kwargs.get("cwd")
        self.core_hidden_dim = kwargs.get("core_hidden_dim")
        self.head_hidden_dim = kwargs.get("head_hidden_dim")
        self.n_agents = SCENARIO_CONFIG[scenario_name]["num_agents"]
        self.use_beta = kwargs.get("use_beta")
        self.use_proj = kwargs.get("use_proj")
        self.no_stand = kwargs.get("no_stand")

        self.encoder_type = kwargs.get("encoder")
        encoding_dim = kwargs.get("encoding_dim")
        encoder_file = kwargs.get("encoder_file")
        self.encoder_loss = kwargs.get("encoder_loss")

        obs_size = observation_space.shape[0] // self.n_agents

        if self.use_proj is True:
            self.proj = torch.load(f'{cwd}/scalers/proj_{scenario_name}.pt', map_location=torch.device(device))

        # Load data scaling variables
        if self.no_stand is False:
            self.data_mean = torch.load(f'{cwd}/scalers/mean_{scenario_name}.pt', map_location=torch.device(device))
            self.data_std = torch.load(f'{cwd}/scalers/std_{scenario_name}.pt', map_location=torch.device(device))

            # Match dimensions if agent size is larger than training
            n = self.n_agents // self.data_mean.shape[0] + 1
            self.data_mean = self.data_mean.repeat(n, 1)[:self.n_agents]
            self.data_std = self.data_std.repeat(n, 1)[:self.n_agents]

        # Load the set autoencoder if provided, or construct a new one if not.
        if self.encoder_type is not None:
            if encoder_file is not None:
                self.autoencoder = torch.load(
                    encoder_file,
                    map_location=torch.device(device)
                )
                print(
                    f"Loaded {self.encoder_type} with dim {self.autoencoder.encoder.input_dim} and hidden_dim {self.autoencoder.encoder.hidden_dim} from"
                    f"disk at {encoder_file}")
                assert self.autoencoder.encoder.hidden_dim == encoding_dim
            else:
                if self.encoder_type == "sae":
                    self.autoencoder = SAE(
                        dim=self.proj.shape[-1] if self.use_proj else obs_size,
                        hidden_dim=encoding_dim,
                    ).to(device)
                else:
                    raise NotImplementedError
                print(f"Constructed randomly initialised {self.encoder_type} with dim {obs_size} and hidden_dim {encoding_dim}")

            # Freeze encoder
            if self.encoder_loss is None:
                for p in self.autoencoder.parameters():
                    p.requires_grad = False
                print(f"Froze {self.encoder_type} parameters")
            else:
                print(f"Did not freeze {self.encoder_type}. Training with loss {self.encoder_loss}.")

        if self.encoder_type is None:
            if self.use_proj is False:
                input_dim = obs_size * self.n_agents + obs_size
            else:
                input_dim = self.proj.shape[1] * self.n_agents + obs_size
        else:
            input_dim = encoding_dim + obs_size

        self.core_network = torch.nn.Sequential(
            torch.nn.Linear(
                in_features=input_dim,
                out_features=self.core_hidden_dim,
            ),
            torch.nn.Tanh(),
            torch.nn.Linear(
                in_features=self.core_hidden_dim,
                out_features=self.core_hidden_dim,
            ),
            torch.nn.Tanh(),
            # # FIXME: Added additional layer
            # torch.nn.Linear(
            #     in_features=self.core_hidden_dim,
            #     out_features=self.core_hidden_dim,
            # ),
            # torch.nn.Tanh(),
        )

        for layer in self.core_network:
            if isinstance(layer, torch.nn.Linear):
                torch.nn.init.normal_(layer.weight, mean=0.0, std=1.0)
                torch.nn.init.normal_(layer.bias, mean=0.0, std=1.0)

        # Initialise final layer with zero mean and very small variance
        self.policy_head = torch.nn.Linear(
            in_features=self.core_hidden_dim,
            out_features=num_outputs // self.n_agents,  # Discrete: action_space[0].n
        )
        torch.nn.init.normal_(self.policy_head.weight, mean=0.0, std=0.01)
        torch.nn.init.normal_(self.policy_head.bias, mean=0.0, std=0.01)

        # # Initialise final layer with zero mean and very small variance FIXME: Added additional layer
        # self.policy_head = torch.nn.Sequential(
        #     torch.nn.Linear(
        #         in_features=self.core_hidden_dim,
        #         out_features=self.core_hidden_dim,  # Discrete: action_space[0].n
        #     ),
        #     torch.nn.Tanh(),
        #
        # )
        # policy_last = torch.nn.Linear(
        #         in_features=self.core_hidden_dim,
        #         out_features=num_outputs // self.n_agents,  # Discrete: action_space[0].n
        # )
        # torch.nn.init.normal_(policy_last.weight, mean=0.0, std=0.01)
        # torch.nn.init.normal_(policy_last.bias, mean=0.0, std=0.01)
        # self.policy_head.add_module("policy_last", policy_last)


        # Value head
        self.value_head = torch.nn.Linear(
            in_features=self.core_hidden_dim,
            out_features=1
        )
        torch.nn.init.normal_(self.value_head.weight, mean=0.0, std=0.01)
        torch.nn.init.normal_(self.value_head.bias, mean=0.0, std=0.01)

        # # Value head FIXME: Added additional layer
        # self.value_head = torch.nn.Sequential(
        #     torch.nn.Linear(
        #         in_features=self.core_hidden_dim,
        #         out_features=self.core_hidden_dim
        #     ),
        #     torch.nn.Tanh(),
        # )
        # value_last = torch.nn.Linear(
        #     in_features=self.core_hidden_dim,
        #     out_features=1
        # )
        # torch.nn.init.normal_(value_last.weight, mean=0.0, std=0.01)
        # torch.nn.init.normal_(value_last.bias, mean=0.0, std=0.01)
        # self.value_head.add_module("value_last", value_last)

        self.current_value = None

    def forward(self, inputs, state, seq_lens):

        observation, batch, agent_features, n_batches = self.process_flat_obs(inputs["obs_flat"])

        if self.encoder_type is None or self.encoder_type == "mlp":
            observation = observation.reshape(n_batches, -1)  # [batches, agents * obs_size]

        x = observation

        if self.encoder_type is not None:
            # Encode observation
            x = self.autoencoder.encoder(x, batch, n_batches=n_batches)

        logits, values = [], []
        for i in range(self.n_agents):
            p = self.core_network(
                torch.cat(
                    (
                        x.clone(),
                        agent_features[:, i].clone(),
                    ),
                    dim=1,
                )
            )
            values.append(
                self.value_head(p.clone()).squeeze(1)
            )
            logits.append(
                self.policy_head(p.clone())
            )
        self.current_value = torch.stack(values, dim=1)
        logits = torch.cat(logits, dim=1)

        return logits, state

    def value_function(self):
        return self.current_value  # [batches, n_agents]

    def custom_loss(self, policy_loss, loss_inputs):

        if self.encoder_loss == "recon":
            observation, batch, agent_features, n_batches = self.process_flat_obs(loss_inputs["obs"])
            x = observation
            if self.encoder_type is not None:
                _ = self.autoencoder(x, batch=batch)
            sae_loss = self.autoencoder.loss()["loss"]
            return [policy_loss[0] + sae_loss]

        else:
            return policy_loss

    def process_flat_obs(self, observation):

        n_batches = observation.shape[0]
        observation = observation.reshape(n_batches, self.n_agents, -1)  # [batches, agents, obs_size]
        agent_features = observation.clone()

        # Rescale observations
        if self.use_proj is False:
            if self.no_stand is False:
                observation = (observation - self.data_mean) / self.data_std
                observation = torch.nan_to_num(
                    observation, nan=0.0, posinf=0.0, neginf=0.0
                )  # Replace NaNs introduced by zero-division with zero

        if self.use_proj:
            observation = observation @ self.proj  # [batches * agents, proj_size]
            if self.no_stand is False:
                # Rescale observations
                observation = (observation - self.data_mean) / self.data_std
                observation = torch.nan_to_num(
                    observation, nan=0.0, posinf=0.0, neginf=0.0
                )  # Replace NaNs introduced by zero-division with zero
                # TODO: Should agent features also be projected? Would it be shady to not do that?
                # TODO: Is the real solution to have two encoders? One SAE and one single-observation encoder?
                # agent_features = agent_features @ self.proj

        observation = torch.flatten(observation, start_dim=0, end_dim=1)  # [batches * agents, obs_size]
        batch = torch.arange(n_batches, device=observation.device).repeat_interleave(self.n_agents)

        return observation, batch, agent_features, n_batches
