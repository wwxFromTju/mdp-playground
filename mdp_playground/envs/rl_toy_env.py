
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import sys
import warnings
import logging
# import os
import copy
from datetime import datetime
import numpy as np
import scipy
from scipy import stats
import gym
from gym.spaces import BoxExtended, DiscreteExtended, MultiDiscreteExtended
# from gym.utils import seeding


class RLToyEnv(gym.Env):
    """
    The base environment in MDP Playground. It is parameterised and can be instantiated to be an MDP with any of the possible meta-features.

    The configuration for the environment is passed as a dict and contains all the information needed to determine the dynamics of the MDP the instantiated object will emulate. We list here the meta-features:
        delay:
        sequence_length:
        transition_noise:
        reward_noise:
        sparsity:
        reward_unit/reward_scale:
        terminal_state_density:
        term_state_reward:
        term_state_edge:
        state_space_relevant_indices:
        action_space_relevant_indices:

    For more details on what each meta-feature means, please refer to the paper at: https://arxiv.org/abs/1909.07750.

    Instead of implementing a new class for every type of MDP, the intent is to capture as many common meta-features across different types of environments as possible and to be able to control the difficulty of an envrionment by allowing fine-grained control over each of these meta-features. The focus is to be as flexible as possible. Mixed continuous and discrete state and action spaces are currently not supported.
    The class extends OpenAI Gym's environment. Below, we list the important attributes and methods for this class.

    Attributes
    ----------
    config : dict
        the config contains all the details required to generate an environment
    seed : int or dict
        recommended to set to int, which would set seeds for the env, relevant and irrelevant and externally visible observation and action spaces automatically. If fine-grained control over the seeds is necessary, a dict, with key values as in the source code further below, can be passed
    rewardable_sequences : list of lists of lists
        holds the rewardable sequences. Here, the 1st index is over different variable sequence lengths (to be able to support variable sequence lengths in the future), the 2nd index is for the diff. sequences possible for that sequence length, the 3rd index is over the sequence itself.
    possible_remaining_sequences : list of lists of lists
        holds, at the current time step, the sequences which might be rewarded at the next time step. Intended to prune out from all the possible rewardable sequences, only the ones we may currently be on. Indices correspond to the ones for rewardable_sequences.

    Methods
    -------
    init_terminal_states()
        Initialises terminal states, T
    init_init_state_dist()
        Initialises initial state distribution, rho_0
    init_transition_function()
        Initialises transition function, P
    init_reward_function()
        Initialises reward function, R
    transition_function(state, action, only_query=True)
        the transition function of the MDP, P
    P(state, action)
        defined as a lambda function in the call to init_transition_function() and is equivalent to calling transition_function() with only_query = False
    reward_function(state, action, only_query=True)
        the reward function of the MDP, R
    R(state, action)
        defined as a lambda function in the call to init_reward_function() and is equivalent to calling reward_function() with only_query = False
    get_augmented_state()
        gets underlying Markovian state of the MDP
    reset()
        Resets enviroment state
    seed()
        Sets the seed for the numpy RNG used by the enviroment (state and action spaces have their own seeds as well)
    step(action)
        Performs 1 transition of the MDP
    """

    def __init__(self, config = None):
        """Initialises the MDP to be emulated using the settings provided in config

        Parameters
        ----------
        config : dict
            the class attribute above is initialised to this value after inserting defaults
        """

        # Set default settings for config to be able to use class without any config passed
        if config is None:
            config = {}

            # Discrete spaces configs:
            config["state_space_type"] = "discrete" # TODO if states are assumed categorical in discrete setting, need to have an embedding for their OHE when using NNs; do the encoding on the training end!
            config["action_space_type"] = "discrete"
            config["state_space_size"] = 6 # To be given as an integer for simple Discrete environment like Gym's. To be given as a list of integers for a MultiDiscrete environment like Gym's #TODO Rename state_space_size and action_space_size to be relevant_... wherever irrelevant dimensions are not used.
            config["action_space_size"] = 6

            # Continuous spaces configs:
            # config["state_space_type"] = "continuous"
            # config["action_space_type"] = "continuous"
            # config["state_space_dim"] = 2
            # config["action_space_dim"] = 2
            # config["transition_dynamics_order"] = 1
            # config["inertia"] = 1 # 1 unit, e.g. kg for mass, or kg * m^2 for moment of inertia.
            # config["state_space_max"] = 5 # Will be a Box in the range [-max, max]
            # config["action_space_max"] = 5 # Will be a Box in the range [-max, max]
            # config["time_unit"] = 0.01 # Discretization of time domain
            config["terminal_states"] = [[0.0, 1.0], [1.0, 0.0]]
            config["term_state_edge"] =  1.0 # Terminal states will be in a hypercube centred around the terminal states given above with the edge of the hypercube of this length.

            # config for user specified P, R, rho_0, T. Examples here are for discrete spaces
            config["transition_function"] = np.array([[4 - i for i in range(config["state_space_size"])] for j in range(config["action_space_size"])]) #TODO ###IMP For all these prob. dist., there's currently a difference in what is returned for discrete vs continuous!
            config["reward_function"] = np.array([[4 - i for i in range(config["state_space_size"])] for j in range(config["action_space_size"])])
            config["init_state_dist"] = np.array([i/10 for i in range(config["state_space_size"])])
            config["is_terminal_state"] = np.array([config["state_space_size"] - 1]) # Can be discrete array or function to test terminal or not (e.g. for discrete and continuous spaces we may prefer 1 of the 2) #TODO currently always the same terminal state for a given environment state space size; have another variable named terminal_states to make semantic sense of variable name.


            config["generate_random_mdp"] = True ###IMP # This supersedes previous settings and generates a random transition function, a random reward function (for random specific sequences)
            config["delay"] = 0
            config["sequence_length"] = 3
            config["repeats_in_sequences"] = False
            config["reward_unit"] = 1.0
            config["reward_density"] = 0.25 # Number between 0 and 1
#            config["transition_noise"] = 0.2 # Currently the fractional chance of transitioning to one of the remaining states when given the deterministic transition function - in future allow this to be given as function; keep in mind that the transition function itself could be made a stochastic function - does that qualify as noise though?
#            config["reward_noise"] = lambda a: a.normal(0, 0.1) #random #hack # a probability function added to reward function
            # config["transition_noise"] = lambda a: a.normal(0, 0.1) #random #hack # a probability function added to transition function in cont. spaces
            config["make_denser"] = False
            config["terminal_state_density"] = 0.1 # Number between 0 and 1
            config["completely_connected"] = True # Make every state reachable from every state; If completely_connected, then no. of actions has to be at least equal to no. of states( - 1 if without self-loop); if repeating sequences allowed, then we have to have self-loops. Self-loops are ok even in non-repeating sequences - we just have a harder search problem then! Or make it maximally connected by having transitions to as many different states as possible - the row for P would have as many different transitions as possible!
            # print(config)
            #TODO asserts for the rest of the config settings
            # next: To implement delay, we can keep the previous observations to make state Markovian or keep an info bit in the state to denote that; Buffer length increase by fixed delay and fixed sequence length; current reward is incremented when any of the satisfying conditions (based on previous states) matches

            #seed old default settings used for paper, etc.:
            config["seed"] = {}
            config["seed"]["env"] = 0 # + config["seed"]
            config["seed"]["state_space"] = config["relevant_state_space_size"] # + config["seed"] for discrete, 10 + config["seed"] in case of continuous
            config["seed"]["action_space"] = 1 + config["relevant_action_space_size"] # + config["seed"] for discrete, 11 + config["seed"] in case of continuous
            # 12 + config["seed"] for continuous self.term_spaces

        # Set other default settings for config to use if config is passed without any values for them
        if "log_level" not in config:
            config["log_level"] = logging.CRITICAL #logging.NOTSET

        if "log_filename" not in config:
            config["log_filename"] = __name__ + '_' + datetime.today().strftime('%m.%d.%Y_%I:%M:%S_%f') + '.log' #TODO Make a directoy 'log/' and store there.
        # log_filename = "logs/output.log"
        # os.makedirs(os.path.dirname(log_filename), exist_ok=True)

        logging.basicConfig(filename=config["log_filename"], filemode='a', format='%(message)s - %(levelname)s - %(name)s - %(asctime)s', datefmt='%m.%d.%Y %I:%M:%S %p', level=config["log_level"])
        self.logger = logging.getLogger(__name__)


        if "seed" not in config:
            config["seed"] = None #seed
        if not isinstance(config["seed"], dict): # should be an int then. Gym doesn't accept np.int64, etc..
            seed_ = config["seed"]
            config["seed"] = {}
            config["seed"]["env"] = seed_
            self.seed(config["seed"]["env"]) #seed
            ##IMP All these diff. seeds may not be needed (you could have one seed for the joint relevant + irrelevant parts). But they allow for easy separation of the relevant and irrelevant dimensions!! _And_ the seed remaining the same for the underlying discrete environment makes it easier to write tests!
            config["seed"]["relevant_state_space"] = self.np_random.randint(sys.maxsize) #random
            config["seed"]["relevant_action_space"] = self.np_random.randint(sys.maxsize) #random
            config["seed"]["irrelevant_state_space"] = self.np_random.randint(sys.maxsize) #random
            config["seed"]["irrelevant_action_space"] = self.np_random.randint(sys.maxsize) #random
            config["seed"]["state_space"] = self.np_random.randint(sys.maxsize) #IMP This is currently used to sample only for continuous spaces and not used for discrete spaces by the Environment. User might want to sample from it for multi-discrete environments. #random
            config["seed"]["action_space"] = self.np_random.randint(sys.maxsize) #IMP This IS currently used to sample random actions by the RL agent for both discrete and continuous environments (but not used anywhere by the Environment). #random
        else: # if seed dict was passed
            self.seed(config["seed"]["env"]) #seed

        logging.warning('Seeds set to:' + str(config["seed"]))
        # print(f'Seeds set to {config["seed"]=}') # Available from Python 3.8


        if "term_state_reward" not in config:
            config["term_state_reward"] = 0.0

        if "delay" not in config:
            config["delay"] = 0

        if "sequence_length" not in config:
            config["sequence_length"] = 1

        if "reward_unit" not in config:
            config["reward_unit"] = 1.0

        #TODO Make below code more compact by reusing parts for state and action spaces?
        config["state_space_type"] = config["state_space_type"].lower()
        config["action_space_type"] = config["action_space_type"].lower()

        if "state_space_relevant_indices" not in config:
            config["state_space_relevant_indices"] = range(config["state_space_size"]) if config["state_space_type"] == "discrete" else range(config["state_space_dim"])
        else:
            pass

        if "action_space_relevant_indices" not in config:
            config["action_space_relevant_indices"] = range(config["action_space_size"]) if config["action_space_type"] == "discrete" else range(config["action_space_dim"])
        else:
            pass

        if ("init_state_dist" in config) and ("relevant_init_state_dist" not in config):
            config["relevant_init_state_dist"] = config["init_state_dist"]

        # Set up the irrelevant dimensions parts: state space
        if config["state_space_type"] == "discrete":
            if isinstance(config["state_space_size"], list):
                config["state_space_multi_discrete_sizes"] = config["state_space_size"]
                self.relevant_state_space_maxes = np.array(config["state_space_size"])[np.array(config["state_space_relevant_indices"])]
                config["relevant_state_space_size"] = int(np.prod(self.relevant_state_space_maxes))
                config["state_space_irrelevant_indices"] = list(set(range(len(config["state_space_size"]))) - set(config["state_space_relevant_indices"]))
                if len(config["state_space_irrelevant_indices"]) == 0:
                    config["irrelevant_state_space_size"] = 0
                else:
                    self.irrelevant_state_space_maxes = np.array(config["state_space_size"])[np.array(config["state_space_irrelevant_indices"])]
                    # self.irrelevant_state_space_maxes = np.array(self.irrelevant_state_space_maxes)
                    config["irrelevant_state_space_size"] = int(np.prod(self.irrelevant_state_space_maxes))
            else: # if simple Discrete environment with the single "dimension" relevant
                assert type(config["state_space_size"]) == int, 'config["state_space_size"] has to be provided as an int when we have a simple Discrete environment. Was:' + str(type(config["state_space_size"]))
                config["relevant_state_space_size"] = config["state_space_size"]
                config["irrelevant_state_space_size"] = 0
            self.logger.info('config["relevant_state_space_size"] inited to:' + str(config["relevant_state_space_size"]))
            self.logger.info('config["irrelevant_state_space_size"] inited to:' + str(config["irrelevant_state_space_size"]))
        else: # if continuous environment
            pass

        # Set up the irrelevant dimensions parts: action space
        if config["action_space_type"] == "discrete":
            if isinstance(config["action_space_size"], list):
                config["action_space_multi_discrete_sizes"] = config["action_space_size"]
                self.relevant_action_space_maxes = np.array(config["action_space_size"])[np.array(config["action_space_relevant_indices"])]
                config["relevant_action_space_size"] = int(np.prod(self.relevant_action_space_maxes))
                config["action_space_irrelevant_indices"] = list(set(range(len(config["action_space_size"]))) - set(config["action_space_relevant_indices"]))
                if len(config["action_space_irrelevant_indices"]) == 0:
                    config["irrelevant_action_space_size"] = 0
                else:
                    self.irrelevant_action_space_maxes = np.array(config["action_space_size"])[np.array(config["action_space_irrelevant_indices"])]
                    # self.irrelevant_action_space_maxes = np.array(self.irrelevant_action_space_maxes)
                    config["irrelevant_action_space_size"] = int(np.prod(self.irrelevant_action_space_maxes))
            else: # if simple Discrete environment with the single "dimension" relevant
                assert type(config["action_space_size"]) == int, 'config["action_space_size"] has to be provided as an int when we have a simple Discrete environment. Was:' + str(type(config["action_space_size"]))
                config["relevant_action_space_size"] = config["action_space_size"]
                config["irrelevant_action_space_size"] = 0
            self.logger.info('config["relevant_action_space_size"] inited to:' + str(config["relevant_action_space_size"]))
            self.logger.info('config["irrelevant_action_space_size"] inited to:' + str(config["irrelevant_action_space_size"]))
        else: # if continuous environment
            pass


        assert config["action_space_type"] == config["state_space_type"], 'config["state_space_type"] != config["action_space_type"]. Currently mixed space types are not supported.'
        assert config["sequence_length"] > 0, "config[\"sequence_length\"] <= 0. Set to: " + str(config["sequence_length"]) # also should be int
        if "completely_connected" in config and config["completely_connected"]:
            assert config["relevant_state_space_size"] == config["relevant_action_space_size"], "config[\"relevant_state_space_size\"] != config[\"relevant_action_space_size\"]. For completely_connected transition graphs, they should be equal. Please provide valid values. Vals: " + str(config["relevant_state_space_size"]) + " " + str(config["relevant_action_space_size"]) + ". In future, \"maximally_connected\" graphs are planned to be supported!"
            assert config["irrelevant_state_space_size"] == config["irrelevant_action_space_size"], "config[\"irrelevant_state_space_size\"] != config[\"irrelevant_action_space_size\"]. For completely_connected transition graphs, they should be equal. Please provide valid values! Vals: " + str(config["irrelevant_state_space_size"]) + " " + str(config["irrelevant_action_space_size"]) + ". In future, \"maximally_connected\" graphs are planned to be supported!" #TODO Currently, iirelevant dimensions have a P similar ot that of relevant dimensions. Should this be decoupled?

        if config["state_space_type"] == 'continuous':
            assert config["state_space_dim"] == config["action_space_dim"], "For continuous spaces, state_space_dim has to be = action_space_dim. state_space_dim was: " + str(config["state_space_dim"]) + " action_space_dim was: " + str(config["action_space_dim"])
            assert config["state_space_relevant_indices"] == config["action_space_relevant_indices"], "For continuous spaces, state_space_relevant_indices has to be = action_space_relevant_indices. state_space_relevant_indices was: " + str(config["state_space_relevant_indices"]) + " action_space_relevant_indices was: " + str(config["action_space_relevant_indices"])
            if config["reward_function"] == "move_to_a_point":
                config["target_point"] = np.array(config["target_point"])
                assert config["target_point"].shape == (len(config["state_space_relevant_indices"]),), "target_point should have dimensionality = relevant_state_space dimensionality"

        self.config = config
        self.sequence_length = config["sequence_length"]
        self.delay = config["delay"]
        self.augmented_state_length = config["sequence_length"] + config["delay"] + 1
        if self.config["state_space_type"] == "discrete":
            self.reward_unit = self.config["reward_unit"]
        else: # cont. spaces
            self.dynamics_order = self.config["transition_dynamics_order"]
            self.inertia = self.config["inertia"]
            self.time_unit = self.config["time_unit"]
            if "reward_scale" in self.config:
                self.reward_scale = self.config["reward_scale"] ##TODO assert for which configurations should be available for which types of environments
            if "reward_unit" in self.config:
                self.reward_unit = self.config["reward_unit"]

        self.total_episodes = 0

        if config["state_space_type"] == "discrete":
            if config["irrelevant_state_space_size"] > 0:
                self.relevant_observation_space = DiscreteExtended(config["relevant_state_space_size"], seed=config["seed"]["relevant_state_space"]) #seed # hack
                self.irrelevant_observation_space = DiscreteExtended(config["irrelevant_state_space_size"], seed=config["seed"]["irrelevant_state_space"]) #seed # hack
                self.observation_space = MultiDiscreteExtended(config["state_space_size"], seed=config["seed"]["state_space"]) #seed # hack #TODO Gym (and so Ray) apparently needs "observation"_space as a member. I'd prefer "state"_space
            else:
                self.observation_space = DiscreteExtended(config["relevant_state_space_size"], seed=config["seed"]["relevant_state_space"]) #seed # hack
                self.relevant_observation_space = self.observation_space
                # print('id(self.observation_space)', id(self.observation_space), 'id(self.relevant_observation_space)', id(self.relevant_observation_space), id(self.relevant_observation_space) == id(self.observation_space))

        else: # cont. spaces
            self.state_space_max = config["state_space_max"] if 'state_space_max' in config else np.inf # should we select a random max? #test?

            self.observation_space = BoxExtended(-self.state_space_max, self.state_space_max, shape=(config["state_space_dim"], ), seed=config["seed"]["state_space"], dtype=np.float64) #seed # hack #TODO # low and high are 1st 2 and required arguments for instantiating BoxExtended


        if config["action_space_type"] == "discrete":
            if config["irrelevant_state_space_size"] > 0: # This has to be for irrelevant_state_space_size and not irrelevant_action_space_size, because there has to be at least 1 irrelevant state to allow transitions in that space!
                self.relevant_action_space = DiscreteExtended(config["relevant_action_space_size"], seed=config["seed"]["relevant_action_space"]) #seed # hack
                self.irrelevant_action_space = DiscreteExtended(config["irrelevant_action_space_size"], seed=config["seed"]["irrelevant_action_space"]) #seed # hack
                self.action_space = MultiDiscreteExtended(config["action_space_size"], seed=config["seed"]["action_space"]) #seed # hack
            else:
                self.action_space = DiscreteExtended(config["relevant_action_space_size"], seed=config["seed"]["relevant_action_space"]) #seed # hack #TODO
                self.relevant_action_space = self.action_space

        else: # cont. spaces
            self.action_space_max = config["action_space_max"] if 'action_space_max' in config else np.inf #test?
            # config["action_space_max"] = num_to_list(config["action_space_max"]) * config["action_space_dim"]
            self.action_space = BoxExtended(-self.action_space_max, self.action_space_max, shape=(config["action_space_dim"], ), seed=config["seed"]["action_space"], dtype=np.float64) #seed # hack #TODO


        if not config["generate_random_mdp"]:
            self.P = config["transition_function"] if callable(config["transition_function"]) else lambda s, a: config["transition_function"][s, a] # callable may not be optimal always since it was deprecated in Python 3.0 and 3.1
            self.R = config["reward_function"] if callable(config["reward_function"]) else lambda s, a: config["reward_function"][s, a]
        else:
            #TODO Generate state and action space sizes also randomly?
            ###IMP The order in which the following inits are called is important, so don't change!!
            self.init_terminal_states()
            self.init_init_state_dist() #init_state_dist: Initialises uniform distribution over non-terminal states for discrete distribution; After looking into Gym code, I can say that for continuous, it's uniform over non-terminal if limits are [a, b], shifted exponential if exactly one of the limits is np.inf, normal if both limits are np.inf - this sampling is independent for each dimension (and is done for the defined limits for the respective dimension).
            self.init_transition_function()
            self.init_reward_function()

        self.curr_state = self.reset() #TODO Maybe not call it here, since Gym seems to expect to _always_ call this method when using an environment; make this seedable? DO NOT do seed dependent initialization in reset() otherwise the initial state distrbution will always be at the same state at every call to reset()!! (Gym env has its own seed? Yes, it does, as does also space);

        self.logger.info("self.augmented_state, len" + str(self.augmented_state) + str(len(self.augmented_state)))
        self.logger.info("MDP Playground toy env instantiated with config:" + str(self.config)) #hack


    def init_terminal_states(self):
        """Initialises terminal state set to be the 'last' states for discrete environments. For continuous environments, terminal states will be in a hypercube centred around config['terminal_states'] with the edge of the hypercube of length config['term_state_edge'].

        """
        if self.config["state_space_type"] == "discrete":
            self.num_terminal_states = int(self.config["terminal_state_density"] * self.config["relevant_state_space_size"])
            if self.num_terminal_states == 0: # Have at least 1 terminal state
                warnings.warn("WARNING: int(terminal_state_density * relevant_state_space_size) was 0. Setting num_terminal_states to be 1!")
                self.num_terminal_states = 1
            self.config["is_terminal_state"] = np.array([self.config["relevant_state_space_size"] - 1 - i for i in range(self.num_terminal_states)]) # terminal states inited to be at the "end" of the sorted states
            self.logger.info("Inited terminal states to self.config['is_terminal_state']:" + str(self.config["is_terminal_state"]) + "total" + str(self.num_terminal_states))
            self.is_terminal_state = self.config["is_terminal_state"] if callable(self.config["is_terminal_state"]) else lambda s: s in self.config["is_terminal_state"]

        else: # if continuous space
            # print("# TODO for cont. spaces: term states")
            self.term_spaces = []

            if 'terminal_states' in self.config: ##TODO For continuous spaces, could also generate terminal spaces based on a terminal_state_density given by user (Currently the user has to design the terminal states they specify such that they would have a given density in space.). But only for Boxes with limits? For Boxes without limits, could do it for a limited subspace of the inifinite Box 1st and then repeat that pattern indefinitely along each dimension's axis. #test?
                for i in range(len(self.config["terminal_states"])): # List of centres of terminal state regions.
                    assert len(self.config["terminal_states"][i]) == len(self.config["state_space_relevant_indices"]), "Specified terminal state centres should have dimensionality = number of state_space_relevant_indices. That was not the case for centre no.: " + str(i) + ""
                    lows = np.array([self.config["terminal_states"][i][j] - self.config["term_state_edge"]/2 for j in range(len(self.config["state_space_relevant_indices"]))])
                    highs = np.array([self.config["terminal_states"][i][j] + self.config["term_state_edge"]/2 for j in range(len(self.config["state_space_relevant_indices"]))])
                    # print("Term state lows, highs:", lows, highs)
                    self.term_spaces.append(BoxExtended(low=lows, high=highs, seed=self.seed_, dtype=np.float64)) #seed #hack #TODO
                self.logger.debug("self.term_spaces samples:" + str(self.term_spaces[0].sample()) + str(self.term_spaces[-1].sample()))

            self.is_terminal_state = lambda s: np.any([self.term_spaces[i].contains(s[self.config["state_space_relevant_indices"]]) for i in range(len(self.term_spaces))]) ### TODO for cont. #test?


    def init_init_state_dist(self):
        """Initialises initial state distrbution, rho_0, to be uniform over the non-terminal states for discrete environments. For both discrete and continuous environemnts, the uniform sampling over non-terminal states is taken care of in reset() when setting the initial state for an episode.

        """
        # relevant dimensions part
        if self.config["state_space_type"] == "discrete":
            non_term_relevant_state_space_size = self.config["relevant_state_space_size"] - self.num_terminal_states
            self.config["relevant_init_state_dist"] = np.array([1 / (non_term_relevant_state_space_size) for i in range(non_term_relevant_state_space_size)] + [0 for i in range(self.num_terminal_states)]) #TODO Currently only uniform distribution over non-terminal states; Use Dirichlet distribution to select prob. distribution to use?
            #TODO make init_state_dist the default sample() for state space?
            self.logger.info("self.relevant_init_state_dist:" + str(self.config["relevant_init_state_dist"]))
        else: # if continuous space
            pass # this is handled in reset where we resample if we sample a term. state

        #irrelevant dimensions part
        if self.config["state_space_type"] == "discrete":
            if self.config["irrelevant_state_space_size"] > 0:
                self.config["irrelevant_init_state_dist"] = np.array([1 / (self.config["irrelevant_state_space_size"]) for i in range(self.config["irrelevant_state_space_size"])]) #TODO Currently only uniform distribution over non-terminal states; Use Dirichlet distribution to select prob. distribution to use!
                self.logger.info("self.irrelevant_init_state_dist:" + str(self.config["irrelevant_init_state_dist"]))


    def init_transition_function(self):
        """Initialises transition function, P by selecting random next states for every (state, action) tuple for discrete environments. For continuous environments, we have 1 option for the transition function which varies depending on dynamics order and inertia and time_unit for a point object.

        """

        # relevant dimensions part
        if self.config["state_space_type"] == "discrete":
            self.config["transition_function"] = np.zeros(shape=(self.config["relevant_state_space_size"], self.config["relevant_action_space_size"]), dtype=object)
            self.config["transition_function"][:] = -1 #IMP # To avoid having a valid value from the state space before we actually assign a usable value below!
            if self.config["completely_connected"]:
                for s in range(self.config["relevant_state_space_size"]):
                    self.config["transition_function"][s] = self.relevant_observation_space.sample(size=self.config["relevant_action_space_size"], replace=False) #random #TODO Preferably use the seed of the Env for this?
            else:
                for s in range(self.config["relevant_state_space_size"]):
                    for a in range(self.config["relevant_action_space_size"]):
                        self.config["transition_function"][s, a] = self.relevant_observation_space.sample() #random #TODO Preferably use the seed of the Env for this?
            for s in range(self.config["relevant_state_space_size"] - self.num_terminal_states, self.config["relevant_state_space_size"]):
                for a in range(self.config["relevant_action_space_size"]):
                    assert self.is_terminal_state(s) == True
                    self.config["transition_function"][s, a] = s # Setting P(s, a) = s for terminal states, for P() to be meaningful even if someone doesn't check for 'done' being = True

            self.logger.info(str(self.config["transition_function"]) + "init_transition_function" + str(type(self.config["transition_function"][0, 0])))
        else: # if continuous space
            # self.logger.debug("# TODO for cont. spaces") # transition function is fixed parameterisation for cont. right now.
            pass

        #irrelevant dimensions part
        if self.config["state_space_type"] == "discrete":
            if self.config["irrelevant_state_space_size"] > 0: # What about irrelevant_ACTION_space_size > 0? Doesn't matter because only if an irrelevant state exists will an irrelevant action be used. #test to see if setting either irrelevant space to 0 causes crashes.
                self.config["transition_function_irrelevant"] = np.zeros(shape=(self.config["irrelevant_state_space_size"], self.config["irrelevant_action_space_size"]), dtype=object)
                self.config["transition_function_irrelevant"][:] = -1 #IMP # To avoid having a valid value from the state space before we actually assign a usable value below!
                if self.config["completely_connected"]:
                    for s in range(self.config["irrelevant_state_space_size"]):
                        self.config["transition_function_irrelevant"][s] = self.irrelevant_observation_space.sample(size=self.config["irrelevant_action_space_size"], replace=False) #random #TODO Preferably use the seed of the Env for this?
                else:
                    for s in range(self.config["irrelevant_state_space_size"]):
                        for a in range(self.config["irrelevant_action_space_size"]):
                            self.config["transition_function_irrelevant"][s, a] = self.irrelevant_observation_space.sample() #random #TODO Preferably use the seed of the Env for this?

                self.logger.info(str(self.config["transition_function_irrelevant"]) + "init_transition_function _irrelevant" + str(type(self.config["transition_function_irrelevant"][0, 0])))


        self.P = lambda s, a: self.transition_function(s, a, only_query = False)

    def init_reward_function(self):
        """Initialises reward function, R by selecting random sequences to be rewardable for discrete environments. For continuous environments, we have fixed available options for the reward function.

        """

        #TODO Maybe refactor this code and put useful reusable permutation generators, etc. in one library
        if self.config["state_space_type"] == "discrete":
            non_term_relevant_state_space_size = self.config["relevant_state_space_size"] - self.num_terminal_states
            if self.config["repeats_in_sequences"]:
                num_possible_sequences = (self.relevant_observation_space.n - self.num_terminal_states) ** self.config["sequence_length"] #TODO if sequence cannot have replacement, use permutations; use state + action sequences? Subtracting the no. of terminal states from state_space size here to get "usable" states for sequences, having a terminal state even at end of reward sequence doesn't matter because to get reward we need to transition to next state which isn't possible for a terminal state.
                num_specific_sequences = int(self.config["reward_density"] * num_possible_sequences) #FIX Could be a memory problem if too large state space and too dense reward sequences
                self.specific_sequences = [[] for i in range(self.sequence_length)]
                sel_sequence_nums = self.np_random.choice(num_possible_sequences, size=num_specific_sequences, replace=False) #random # This assumes that all sequences have an equal likelihood of being selected for being a reward sequence;
                for i in range(num_specific_sequences):
                    curr_sequence_num = sel_sequence_nums[i]
                    specific_sequence = []
                    while len(specific_sequence) != self.config["sequence_length"]:
                        specific_sequence.append(curr_sequence_num % (non_term_relevant_state_space_size)) ####TODO
                        curr_sequence_num = curr_sequence_num // (non_term_relevant_state_space_size)
                    #bottleneck When we sample sequences here, it could get very slow if reward_density is high; alternative would be to assign numbers to sequences and then sample these numbers without replacement and take those sequences
                    # specific_sequence = self.relevant_observation_space.sample(size=self.config["sequence_length"], replace=True) # Be careful that sequence_length is less than state space size
                    self.specific_sequences[self.sequence_length - 1].append(specific_sequence) #hack
                    self.logger.info("specific_sequence that will be rewarded" + str(specific_sequence)) #TODO impose a different distribution for these: independently sample state for each step of specific sequence; or conditionally dependent samples if we want something like DMPs/manifolds
                self.logger.info("Total no. of rewarded sequences:" + str(len(self.specific_sequences[self.sequence_length - 1])) + str("Out of", num_possible_sequences))
            else: # if no repeats_in_sequences
                len_ = self.sequence_length
                permutations = list(range(non_term_relevant_state_space_size + 1 - len_, non_term_relevant_state_space_size + 1))
                self.logger.info("No. of choices for each element in a possible sequence (Total no. of permutations will be a product of this), 1 random number out of possible perms, no. of possible perms" + str(permutations) + str(np.random.randint(np.prod(permutations))) + str(np.prod(permutations))) #random
                num_possible_permutations = np.prod(permutations)
                num_specific_sequences = int(self.config["reward_density"] * num_possible_permutations)
                if num_specific_sequences > 1000:
                    warnings.warn('Too many rewardable sequences and/or too long rewardable sequence length. Environment might be too slow. Please consider setting the reward_density to be lower or reducing the sequence length. No. of rewardable sequences:', num_specific_sequences) #TODO Maybe even exit the program if too much memory is (expected to be) taken.

                self.specific_sequences = [[] for i in range(self.sequence_length)]
                sel_sequence_nums = self.np_random.choice(num_possible_permutations, size=num_specific_sequences, replace=False) #random # This assumes that all sequences have an equal likelihood of being selected for being a reward sequence; # TODO this code could be replaced with self.np_random.permutation(non_term_relevant_state_space_size)[self.sequence_length]? Replacement becomes a problem then! We have to keep smpling until we have all unique rewardable sequences.
                total_clashes = 0
                for i in range(num_specific_sequences):
                    curr_permutation = sel_sequence_nums[i]
                    seq_ = []
                    curr_rem_digits = list(range(non_term_relevant_state_space_size)) # has to contain every number up to n so that any one of them can be picked as part of the sequence below
                    for j in permutations[::-1]: # Goes from largest to smallest number in nPk factors
                        rem_ = curr_permutation % j
                        seq_.append(curr_rem_digits[rem_])
                        del curr_rem_digits[rem_]
                #         print("curr_rem_digits", curr_rem_digits)
                        curr_permutation = curr_permutation // j
                #         print(rem_, curr_permutation, j, seq_)
                #     print("T/F:", seq_ in self.specific_sequences)
                    if seq_ in self.specific_sequences[self.sequence_length - 1]: #hack
                        total_clashes += 1 #TODO remove these extra checks and assert below
                    self.specific_sequences[self.sequence_length - 1].append(seq_)
                    self.logger.info("specific_sequence that will be rewarded" + str(seq_))
                #print(len(set(self.specific_sequences))) #error
                # print(self.specific_sequences[self.sequence_length - 1])

                self.logger.debug("Number of generated sequences that did not clash with an existing one when it was generated:" + str(total_clashes))
                assert total_clashes == 0, 'None of the generated sequences should have clashed with an existing rewardable sequence when it was generated. No. of times a clash was detected:' + str(total_clashes)
                self.logger.info("Total no. of rewarded sequences:" + str(len(self.specific_sequences[self.sequence_length - 1])) + "Out of" + str(num_possible_permutations))
        else: # if continuous space
            # self.logger.debug("# TODO for cont. spaces?: init_reward_function") # reward functions are fixed for cont. right now with a few available choices.
            pass

        self.R = lambda s, a: self.reward_function(s, a, only_query=False)


    def transition_function(self, state, action, only_query=True):
        """The transition function, P.

        Performs a transition according to the initialised P for discrete environments (with dynamics independent for relevant vs irrelevant dimension sub-spaces). For continuous environments, we have a fixed available option for the dynamics (which is the same for relevant or irrelevant dimensions):
        The order of the system decides the dynamics. For an nth order system, the nth order derivative of the state is set to the action value / inertia for time_unit seconds. And then the dynamics are integrated over the time_unit to obtain the next state.

        Parameters
        ----------
        state : list
            The state that the environment will use to perform a transition.
        action : list
            The action that the environment will use to perform a transition.
        only_query: boolean
            Option for the user to perform "imaginary" transitions, e.g., for model-based RL. If set to true, underlying augmented state of the MDP is not changed and user is responsible to maintain and provide the state to this function to be able to perform a rollout. For continuous environments, this is NOT currently supported. ##TODO

        Returns
        -------
        int or np.array
            The state at the end of the current transition
        """

        # Transform multi-discrete to discrete for discrete state spaces with irrelevant dimensions
        if self.config["state_space_type"] == "discrete":
            if isinstance(self.config["state_space_size"], list):
                if self.config["irrelevant_state_space_size"] > 0:
                    state, action, state_irrelevant, action_irrelevant = self.multi_discrete_to_discrete(state, action, irrelevant_parts=True)
                else:
                    state, action, _, _ = self.multi_discrete_to_discrete(state, action)

        if self.config["state_space_type"] == "discrete":
            next_state = self.config["transition_function"][state, action]
            if "transition_noise" in self.config:
                probs = np.ones(shape=(self.config["relevant_state_space_size"],)) * self.config["transition_noise"] / (self.config["relevant_state_space_size"] - 1)
                probs[next_state] = 1 - self.config["transition_noise"]
                # TODO Samples according to new probs to get noisy discrete transition
                new_next_state = self.relevant_observation_space.sample(prob=probs) #random
                # print("noisy old next_state, new_next_state", next_state, new_next_state)
                if next_state != new_next_state:
                    self.logger.info("NOISE inserted! old next_state, new_next_state" + str(next_state) + str(new_next_state))
                    self.total_noisy_transitions_episode += 1
                # print("new probs:", probs, self.relevant_observation_space.sample(prob=probs))
                next_state = new_next_state
                # assert np.sum(probs) == 1, str(np.sum(probs)) + " is not equal to " + str(1)

            #irrelevant dimensions part
            if self.config["irrelevant_state_space_size"] > 0:
                if self.config["irrelevant_action_space_size"] > 0: # only if there's an irrelevant action does a transition take place (even a noisy one)
                    next_state_irrelevant = self.config["transition_function_irrelevant"][state_irrelevant, action_irrelevant]
                    if "transition_noise" in self.config:
                        probs = np.ones(shape=(self.config["irrelevant_state_space_size"],)) * self.config["transition_noise"] / (self.config["irrelevant_state_space_size"] - 1)
                        probs[next_state_irrelevant] = 1 - self.config["transition_noise"]
                        new_next_state_irrelevant = self.irrelevant_observation_space.sample(prob=probs) #random
                        # if next_state_irrelevant != new_next_state_irrelevant:
                        #     print("NOISE inserted! old next_state_irrelevant, new_next_state_irrelevant", next_state_irrelevant, new_next_state_irrelevant)
                        #     self.total_noisy_transitions_irrelevant_episode += 1
                        next_state_irrelevant = new_next_state_irrelevant


        else: # if continuous space
            # print("#TODO for cont. spaces: noise")
            assert len(action.shape) == 1, 'Action should be specified as a 1-D tensor. However, shape of action was: ' + str(action.shape)
            assert action.shape[0] == self.config['action_space_dim'], 'Action shape is: ' + str(action.shape[0]) + '. Expected: ' + str(self.config['action_space_dim'])
            if self.action_space.contains(action):
                ### TODO implement for multiple orders, currently only for 1st order systems.
                # if self.dynamics_order == 1:
                #     next_state = state + action * self.time_unit / self.inertia

                # print('self.state_derivatives:', self.state_derivatives)
                # Except the last member of state_derivatives, the other occupy the same place in memory. Could create a new copy of them every time, but I think this should be more efficient and as long as tests don't fail should be fine.
                self.state_derivatives[-1] = action / self.inertia # action is presumed to be n-th order force ##TODO Could easily scale this per dimension to give different kinds of dynamics per dimension: maybe even sample this scale per dimension from a probability distribution to generate different random Ps?
                factorial_array = scipy.special.factorial(np.arange(1, self.config['transition_dynamics_order'] + 1)) # This is just to speed things up as scipy calculates the factorial only for largest array member
                for i in range(self.config['transition_dynamics_order']):
                    for j in range(self.config['transition_dynamics_order'] - i):
                        # print('i, j, self.state_derivatives, (self.time_unit**(j + 1)), factorial_array:', i, j, self.state_derivatives, (self.time_unit**(j + 1)), factorial_array)
                        self.state_derivatives[i] += self.state_derivatives[i + j + 1] * (self.time_unit**(j + 1)) / factorial_array[j] #+ state_derivatives_prev[i] Don't need to add previous value as it's already in there at the beginning ##### TODO Keep an old self.state_derivatives and a new one otherwise higher order derivatives will be overwritten before being used by lower order ones.
                # print('self.state_derivatives:', self.state_derivatives)
                next_state = self.state_derivatives[0]

            else: # if action is from outside allowed action_space
                next_state = state
                warnings.warn("WARNING: Action out of range of action space. Applying 0 action!!")
            # if "transition_noise" in self.config:
            noise_in_transition = self.config["transition_noise"](self.np_random) if "transition_noise" in self.config else 0 #random
            self.total_abs_noise_in_transition_episode += np.abs(noise_in_transition)
            next_state += noise_in_transition ##IMP Noise is only applied to state and not to higher order derivatives
            ### TODO Check if next_state is within state space bounds
            if not self.observation_space.contains(next_state):
                self.logger.info("next_state out of bounds. next_state, clipping to" + str(next_state) + str(np.clip(next_state, -self.state_space_max, self.state_space_max)))
                next_state = np.clip(next_state, -self.state_space_max, self.state_space_max) # Could also "reflect" next_state when it goes out of bounds. Would seem more logical for a "wall", but would need to take care of multiple reflections near a corner/edge.
                # Resets all higher order derivatives to 0
                zero_state = np.array([0.0] * (self.config['state_space_dim']))
                self.state_derivatives = [zero_state.copy() for i in range(self.config['transition_dynamics_order'] + 1)] #####IMP to have copy() otherwise it's the same array (in memory) at every position in the list
                self.state_derivatives[0] = next_state


        if only_query:
            pass
            # print("Only query") # Since transition_function currently depends only on current state and action, we don't need to do anything here!
        else:
            del self.augmented_state[0]
            self.augmented_state.append(next_state.copy() if isinstance(next_state, np.ndarray) else next_state)
            self.total_transitions_episode += 1


        # Transform discrete back to multi-discrete if needed
        if self.config["state_space_type"] == "discrete":
            if isinstance(self.config["state_space_size"], list):
                if self.config["irrelevant_state_space_size"] > 0:
                    next_state = self.discrete_to_multi_discrete(next_state, next_state_irrelevant)
                else:
                    next_state = self.discrete_to_multi_discrete(next_state)

        return next_state

    def reward_function(self, state, action, only_query=True):
        """The reward function, R.

        Rewards the sequences selected to be rewardable at initialisation for discrete environments. For continuous environments, we have fixed available options for the reward function:
            move_to_a_point rewards for moving to a predefined location. It has sparse and dense settings.
            move_along_a_line rewards moving along ANY direction in space as long as it's a fixed direction for sequence_length consecutive steps.

        Parameters
        ----------
        state : list
            The augmented state that the environment uses to calculate its reward. Normally, just the sequence of past states of length delay + sequence_length + 1.
        action : list
            It's currently NOT used to calculate the reward. Since the underlying MDP dynamics are deterministic a state and action map 1-to-1 with the next state and just a sequence of states should be enough to calculate the reward. But it might be useful in the future, e.g., to penalise action magnitudes
        only_query: boolean
            Option for the user to perform "imaginary" transitions, e.g., for model-based RL. If set to true, underlying augmented state of the MDP is not changed and user is responsible to maintain and provide a list of states to this function to be able to perform a rollout.

        Returns
        -------
        double
            The reward at the end of the current transition

        #TODO Make reward depend on the action sequence too instead of just state sequence, as it is currently? Maybe only use the action sequence for penalising action magnitude?
        """

        # Transform multi-discrete to discrete if needed. This is only needed for only_query = True to be able to transform multi-discrete state and action passed by user into underlying discrete state and action!
        if only_query: #test
            if self.config["state_space_type"] == "discrete":
                if isinstance(self.config["state_space_size"], list):
                    # The following part is commented out because irrelevant parts are not needed in the reward_function.
                    # if self.config["irrelevant_state_space_size"] > 0:
                    #     state, action, state_irrelevant, action_irrelevant = self.multi_discrete_to_discrete(state, action, irrelevant_parts=True)
                    # else:
                    for i in range(len(state)):
                        state[i], action, _, _ = self.multi_discrete_to_discrete(state[i], action)

        delay = self.delay
        sequence_length = self.sequence_length
        reward = 0.0
        # print("TEST", self.augmented_state[0 : self.augmented_state_length - delay], state, action, self.specific_sequences, type(state), type(self.specific_sequences))
        state_considered = state if only_query else self.augmented_state # When we imagine a rollout, the user has to provide full augmented state as the argument!!
        # if not isinstance(state_considered, list):
        #     state_considered = [state_considered] # to get around case when sequence is an int; it should always be a list except if a user passes in a state; would rather force them to pass a list: assert for it!!
        # TODO These asserts are only needed if only_query is True, as users then pass in a state sequence
        if only_query:
            assert isinstance(state_considered, list), "state passed in should be a list of states containing at the very least the state at beginning of the transition, s, and the one after it, s'. type was: " + str(type(state_considered))
            assert len(state_considered) == self.augmented_state_length, "Length of list of states passed should be equal to self.augmented_state_length. It was: " + str(len(state_considered))

        if self.config["state_space_type"] == "discrete":
            if not self.config["make_denser"]:
                self.logger.debug(str(state_considered) + "with delay" + str(self.config["delay"]))
                if state_considered[1 : self.augmented_state_length - delay] in self.specific_sequences[self.sequence_length - 1]:
                    # print(state_considered, "with delay", self.config["delay"], "rewarded with:", 1)
                    reward += self.reward_unit
                else:
                    # print(state_considered, "with delay", self.config["delay"], "NOT rewarded.")
                    pass
            else: # if make_denser
                for j in range(1, sequence_length + 1):
            # Check if augmented_states - delay up to different lengths in the past are present in sequence lists of that particular length; if so add them to the list of length
                    curr_seq_being_checked = state_considered[self.augmented_state_length - j - delay : self.augmented_state_length - delay]
                    # print("curr_seq_being_checked, self.possible_remaining_sequences[j - 1]:", curr_seq_being_checked, self.possible_remaining_sequences[j - 1])
                    if curr_seq_being_checked in self.possible_remaining_sequences[j - 1]:
                        count_ = self.possible_remaining_sequences[j - 1].count(curr_seq_being_checked)
                        # print("curr_seq_being_checked, count in possible_remaining_sequences, reward", curr_seq_being_checked, count_, count_ * self.reward_unit * j / self.sequence_length)
                        reward += count_ * self.reward_unit * j / self.sequence_length #TODO Maybe make it possible to choose not to multiply by count_ as a config option

                self.possible_remaining_sequences = [[] for i in range(sequence_length)] #TODO for variable sequence length just maintain a list of lists of lists rewarded_sequences
                for j in range(0, sequence_length):
            #        if j == 0:
                    for k in range(sequence_length):
                        for l in range(len(self.specific_sequences[k])): # self.specific_sequences[i][j][k] where 1st index is over different variable sequence lengths (to be able to support variable sequence lengths in the future), 2nd index is for the diff. sequences possible for that sequence length, 3rd index is over the sequence
                            if state_considered[self.augmented_state_length - j - delay : self.augmented_state_length - delay] == self.specific_sequences[k][l][:j]: # if curr_seq_being_checked matches a rewardable sequence up to a length j in the past, i.e., is a prefix of the rewardable sequence,
                                self.possible_remaining_sequences[j].append(self.specific_sequences[k][l][:j + 1]) # add it + an extra next state in that sequence to list of possible sequence prefixes to be checked for rewards above
                ###IMP: Above routine for sequence prefix checking can be coded in a more human understandable manner, but this kind of pruning out of "sequences which may not be attainable" based on the current past trajectory, done above, should be in principle more efficient?

                self.logger.info("rew" + str(reward))
                self.logger.debug("self.possible_remaining_sequences" + str(self.possible_remaining_sequences))

        else: # if continuous space
            ###TODO Reward for reaching a target point case (with make_dense); Make reward for along a line case to be length of line travelled - sqrt(Sum of Squared distances from the line)? This should help with keeping the mean reward near 0.
            if np.isnan(state_considered[0][0]): # Instead of below commented out check, this is more robust for imaginary transitions
            # if self.total_transitions_episode + 1 < self.augmented_state_length: # + 1 because augmented_state_length is always 1 greater than seq_len + del
                pass #TODO
            else:
                if self.config["reward_function"] == "move_along_a_line":
                    # print("######reward test", self.total_transitions_episode, np.array(self.augmented_state), np.array(self.augmented_state).shape)
                    #test: 1. for checking 0 distance for same action being always applied; 2. similar to 1. but for different dynamics orders; 3. similar to 1 but for different action_space_dims; 4. for a known applied action case, check manually the results of the formulae and see that programmatic results match: should also have a unit version of 4. for dist_of_pt_from_line() and an integration version here for total_dist calc.?.
                    data_ = np.array(state_considered)[1 : self.augmented_state_length - delay, self.config["state_space_relevant_indices"]]
                    data_mean = data_.mean(axis=0)
                    uu, dd, vv = np.linalg.svd(data_ - data_mean)
                    self.logger.info('uu.shape, dd.shape, vv.shape =' + str(uu.shape) + str(dd.shape) + str(vv.shape))
                    line_end_pts = vv[0] * np.linspace(-1, 1, 2)[:, np.newaxis] # vv[0] = 1st eigenvector, corres. to Principal Component #hardcoded -100 to 100 to get a "long" line which should make calculations more robust(?: didn't seem to be the case for 1st few trials, so changed it to -1, 1; even tried up to 10000 - seems to get less precise for larger numbers) to numerical issues in dist_of_pt_from_line() below; newaxis added so that expected broadcasting takes place
                    line_end_pts += data_mean

                    total_dist = 0
                    for data_pt in data_: # find total distance of all data points from the fit line above
                        total_dist += dist_of_pt_from_line(data_pt, line_end_pts[0], line_end_pts[-1])
                    self.logger.info('total_dist of pts from fit line:' + str(total_dist))

                    reward += ( - total_dist / self.sequence_length ) * self.reward_scale

                elif self.config["reward_function"] == "move_to_a_point": # Could generate target points randomly but leaving it to the user to do that. #TODO Generate it randomly to have random Rs?
                    assert self.config["sequence_length"] == 1
                    if self.config["make_denser"] == True:
                        old_relevant_state = np.array(state_considered)[-2 - delay, self.config["state_space_relevant_indices"]]
                        new_relevant_state = np.array(state_considered)[-1 - delay, self.config["state_space_relevant_indices"]]
                        reward = -np.linalg.norm(new_relevant_state - self.config["target_point"]) # Should allow other powers of the distance from target_point, or more norms?
                        reward += np.linalg.norm(old_relevant_state - self.config["target_point"]) # Reward is the distance moved towards the target point.
                        reward *= self.reward_scale
                        # Should rather be the change in distance to target point, so reward given is +ve if "correct" action was taken and so reward function is more natural
                        # It's true that giving the -ve distance as the loss at every step gives a stronger signal to algorithm to make it move faster towards target but this seems more natural. But the value function is then higher for states further from target. But isn't that okay? Since the greater the challenge (i.e. distance from target), the greater is the achieved overall reward at the end.
                        #TODO To enable seq_len, we can hand out reward if distance to target point is reduced (or increased - since that also gives a better signal than giving 0 in that case!!) for seq_len consecutive steps, otherwise 0 reward - however we need to hand out fixed reward for every "sequence" achieved otherwise, if we do it by adding the distance moved towards target in the sequence, it leads to much bigger rewards for larger seq_lens because of overlapping consecutive sequences.
                        #TODO also make_denser, sparse rewards only at target
                    else:
                        new_relevant_state = np.array(state_considered)[-1 - delay, self.config["state_space_relevant_indices"]]
                        if np.linalg.norm(new_relevant_state - self.config["target_point"]) < self.config["target_radius"]:
                            reward = self.reward_unit # Make the episode terminate as well? Don't need to. If algorithm is smart enough, it will stay in the radius and earn more reward.


        noise_in_reward = self.config["reward_noise"](self.np_random) if "reward_noise" in self.config else 0 #random
        self.total_abs_noise_in_reward_episode += np.abs(noise_in_reward)
        self.total_reward_episode += reward
        reward += noise_in_reward
        return reward

    def discrete_to_multi_discrete(self, relevant_part, irrelevant_part=None):
        '''Transforms relevant and irrelevant parts of state (NOT action) space from discrete to its multi-discrete representation which is the externally visible observation_space from the environment when multi-discrete environments are selected.

        #TODO Generalise function to also be able to transform actions. Right now not a priority because actions are not returned in P, only the next state is.
        '''

        relevant_part = transform_discrete_to_multi_discrete(relevant_part, self.relevant_state_space_maxes)
        combined_ = relevant_part
        if self.config["irrelevant_state_space_size"] > 0:
            irrelevant_part = transform_discrete_to_multi_discrete(irrelevant_part, self.irrelevant_state_space_maxes)

            combined_ = np.zeros(shape=(len(self.config['state_space_size']),), dtype=int)
            combined_[self.config["state_space_relevant_indices"]] = relevant_part
            combined_[self.config["state_space_irrelevant_indices"]] = irrelevant_part

        return list(combined_)

    def multi_discrete_to_discrete(self, state, action, irrelevant_parts=False):
        '''Transforms multi-discrete representations of state and action to their discrete equivalents. Needed at the beginnings of P and R to convert externally visible observation_space from the environment to the internally used observation space that is used inside P and R.
        '''

        relevant_part_state = transform_multi_discrete_to_discrete(np.array(state)[np.array(self.config['state_space_relevant_indices'])], self.relevant_state_space_maxes)
        relevant_part_action = transform_multi_discrete_to_discrete(np.array(action)[np.array(self.config['action_space_relevant_indices'])], self.relevant_action_space_maxes)
        irrelevant_part_state = None
        irrelevant_part_action = None

        if irrelevant_parts:
            irrelevant_part_state = transform_multi_discrete_to_discrete(np.array(state)[np.array(self.config['state_space_irrelevant_indices'])], self.irrelevant_state_space_maxes)
            irrelevant_part_action = transform_multi_discrete_to_discrete(np.array(action)[np.array(self.config['action_space_irrelevant_indices'])], self.irrelevant_action_space_maxes)

        return relevant_part_state, relevant_part_action, irrelevant_part_state, irrelevant_part_action

    def reset(self):
        '''Resets the environment for the beginning of an episode and samples a start state from rho_0. For discrete environments uses the defined rho_0 directly. For continuous environments, samples a state and resamples until a non-terminal state is sampled.

        Returns
        -------
        int or np.array
            The start state for a new episode.
        '''

        # on episode "end" stuff (to not be invoked when reset() called when self.total_episodes = 0; end is in quotes because it may not be a true episode end reached by reaching a terminal state, but reset() may have been called in the middle of an episode):
        if not self.total_episodes == 0:
            self.logger.info("Noise stats for previous episode num.:" + str(self.total_episodes) + "(total abs. noise in rewards, total abs. noise in transitions, total reward, total noisy transitions, total transitions):" + str(self.total_abs_noise_in_reward_episode) + str(self.total_abs_noise_in_transition_episode) + str(self.total_reward_episode) + str(self.total_noisy_transitions_episode) + str(self.total_transitions_episode))

        # on episode start stuff:
        self.total_episodes += 1

        if self.config["state_space_type"] == "discrete":
            self.curr_state_relevant = self.np_random.choice(self.config["relevant_state_space_size"], p=self.config["relevant_init_state_dist"]) #random
            self.curr_state = self.curr_state_relevant
            if isinstance(self.config["state_space_size"], list):
                if self.config["irrelevant_state_space_size"] > 0:
                    self.curr_state_irrelevant = self.np_random.choice(self.config["irrelevant_state_space_size"], p=self.config["irrelevant_init_state_dist"]) #random
                    self.curr_state = self.discrete_to_multi_discrete(self.curr_state_relevant, self.curr_state_irrelevant)
                    self.logger.info("RESET called. Relevant part of state reset to:" + str(self.curr_state_relevant))
                    self.logger.info("Irrelevant part of state reset to:" + str(self.curr_state_irrelevant))
                else:
                    self.curr_state = self.discrete_to_multi_discrete(self.curr_state_relevant)
                    self.logger.info("RESET called. Relevant part of state reset to:" + str(self.curr_state_relevant))

            self.logger.info("RESET called. curr_state set to:" + str(self.curr_state))
            self.augmented_state = [np.nan for i in range(self.augmented_state_length - 1)]
            self.augmented_state.append(self.curr_state_relevant)
            # self.augmented_state = np.array(self.augmented_state) # Do NOT make an np.array out of it because we want to test existence of the array in an array of arrays which is not possible with np.array!
        else: # if continuous space
            self.logger.debug("#TODO for cont. spaces: reset")
            while True: # Be careful about infinite loops
                term_space_was_sampled = False
                self.curr_state = self.observation_space.sample() #random
                for i in range(len(self.term_spaces)): # Could this sampling be made more efficient? In general, the non-terminal space could have any shape and assiging equal sampling probability to each point in this space is pretty hard.
                    if self.is_terminal_state(self.curr_state):
                        self.logger.info("A state was sampled in term state subspace. Therefore, resampling. State was, subspace was:" + str(self.curr_state) + str(i)) ##TODO Move this logic into a new class in Gym spaces that can contain subspaces for term states! (with warning/error if term subspaces cover whole state space, or even a lot of it)
                        term_space_was_sampled = True
                        break
                if not term_space_was_sampled:
                    break

            # init the state derivatives needed for continuous spaces
            zero_state = np.array([0.0] * (self.config['state_space_dim']))
            self.state_derivatives = [zero_state.copy() for i in range(self.config['transition_dynamics_order'] + 1)] #####IMP to have copy() otherwise it's the same array (in memory) at every position in the list
            self.state_derivatives[0] = self.curr_state

            self.logger.info("RESET called. State reset to:" + str(self.curr_state))
            self.augmented_state = [[np.nan] * self.config["state_space_dim"] for i in range(self.augmented_state_length - 1)]
            self.augmented_state.append(self.curr_state.copy())

        self.total_abs_noise_in_reward_episode = 0
        self.total_abs_noise_in_transition_episode = 0 # only present in continuous spaces
        self.total_noisy_transitions_episode = 0 # only present in discrete spaces
        self.total_reward_episode = 0
        self.total_transitions_episode = 0


        # This part initializes self.possible_remaining_sequences to hold 1st state in all rewardable sequences, which will be checked for after 1st step of the episode to give rewards.
        if self.config["state_space_type"] == "discrete" and self.config["make_denser"] == True:
            delay = self.delay
            sequence_length = self.sequence_length
            self.possible_remaining_sequences = [[] for i in range(sequence_length)]
            for j in range(1):
            #        if j == 0:
                for k in range(sequence_length):
                    for l in range(len(self.specific_sequences[k])):
    #                    if state_considered[self.augmented_state_length - j - delay : self.augmented_state_length - delay] == self.specific_sequences[k][l][:j]:
                            self.possible_remaining_sequences[j].append(self.specific_sequences[k][l][:j + 1])

            self.logger.debug("self.possible_remaining_sequences" + str(self.possible_remaining_sequences))
            self.logger.info(" self.delay, self.sequence_length:" + str(self.delay) + str(self.sequence_length))

        return self.curr_state

    def step(self, action):
        """The step function for the environment.

        Parameters
        ----------
        action : int or np.array
            The action that the environment will use to perform a transition.

        Returns
        -------
        int or np.array, double, boolean, dict
            The next state, reward, whether the episode terminated and additional info dict at the end of the current transition
        """
        self.curr_state = self.P(self.curr_state, action)
        self.reward = self.R(self.curr_state, action) ### TODO Decide whether to give reward before or after transition ("after" would mean taking next state into account and seems more logical to me) - make it a meta-feature? - R(s) or R(s, a) or R(s, a, s')? I'd say give it after and store the old state in the augmented_state to be able to let the R have any of the above possible forms. That would also solve the problem of implicit 1-step delay with giving it before. _And_ would not give any reward for already being in a rewarding state in the 1st step but _would_ give a reward if 1 moved to a rewardable state - even if called with R(s, a) because s' is stored in the augmented_state! #####IMP


        self.done = self.is_terminal_state(self.curr_state)
        if self.done:
            self.reward += self.config["term_state_reward"]
        self.logger.info('sas\'r:' + str(self.augmented_state[-2]) + '   ' + str(action) + '   ' + str(self.augmented_state[-1]) + '   ' + str(self.reward))
        return self.curr_state, self.reward, self.done, self.get_augmented_state()

    def get_augmented_state(self):
        '''Intended to return the full augmented state which would be Markovian. (However, it's not Markovian wrt the noise in P and R because we're not returning the underlying RNG.) Currently, returns the augmented state which is the sequence of length "delay + sequence_length + 1" of past states for both discrete and continuous environments. Additonally, the current state derivatives are also returned for continuous enviroments.

        Returns
        -------
        dict
            Contains at the end of the current transition

        #TODO For noisy processes, this would need the noise distribution and random seed too. Also add the irrelevant state parts, etc.? We don't need the irrelevant parts for the state to be Markovian.
        '''
        if self.config["state_space_type"] == "discrete":
            augmented_state_dict = {"curr_state": self.curr_state, "augmented_state": self.augmented_state}
        else:
            augmented_state_dict = {"curr_state": self.curr_state, "augmented_state": self.augmented_state, "state_derivatives": self.state_derivatives}
        return augmented_state_dict

    def seed(self, seed=None):
        """Initialises the Numpy RNG for the environment by calling a utility for this in Gym.

        The environment has its own RNG and so do the state and action spaces held by the environment.

        Parameters
        ----------
        seed : int
            seed to initialise the np_random instance held by the environment. Cannot use numpy.int64 or similar because Gym doesn't accept it.

        Returns
        -------
        int
            The seed returned by Gym
        """
        # If seed is None, you get a randomly generated seed from gym.utils...
        self.np_random, self.seed_ = gym.utils.seeding.np_random(seed) #random
        self.logger.info("Env SEED set to:" + str(seed) + "Returned seed from Gym:" + str(self.seed_))
        return self.seed_



def transform_multi_discrete_to_discrete(vector, vector_maxes):
    '''
    Transforms a multi-discrete vector drawn from a multi-discrete space with ranges for each dimension from 0 to vector_maxes to a discrete equivalent with the discrete number drawn from a 1-D space where the min is 0 and the max is np.prod(vector_maxes) - 1. The correspondence between "counting"/ordering in the multi-discrete space with the discrete space assumes that the rightmost element varies most frequently in the multi-discrete space.
    '''
    return np.arange(np.prod(vector_maxes)).reshape(vector_maxes)[tuple(vector)]

def transform_discrete_to_multi_discrete(scalar, vector_maxes):
    '''
    Transforms a discrete scalar drawn from a 1-D space where the min is 0 and the max is np.prod(vector_maxes) - 1 to a multi-discrete equivalent with the multi-discrete vector drawn from a multi-discrete space with ranges for each dimension from 0 to vector_maxes
    '''
    return np.argwhere(np.arange(np.prod(vector_maxes)).reshape(vector_maxes) == scalar).flatten()


def dist_of_pt_from_line(pt, ptA, ptB):
    '''Returns shortest distance of a point from a line defined by 2 points - ptA and ptB. Based on: https://softwareengineering.stackexchange.com/questions/168572/distance-from-point-to-n-dimensional-line
    '''

    lineAB = ptA - ptB
    lineApt = ptA - pt
    dot_product = np.dot(lineAB, lineApt)
    proj = dot_product / np.linalg.norm(lineAB) ####TODO could lead to division by zero if line is a null vector!
    sq_dist = np.linalg.norm(lineApt)**2 - proj**2
    tolerance = -1e-13
    if sq_dist < 0:
        if sq_dist < tolerance:
            logger.warn('The squared distance calculated in dist_of_pt_from_line() using Pythagoras\' theorem was less than the tolerance allowed. It was: ' + str(sq_dist) + '. Tolerance was: ' + str(tolerance))
        sq_dist = 0
    dist = np.sqrt(sq_dist)
#     print('pt, ptA, ptB, lineAB, lineApt, dot_product, proj, dist:', pt, ptA, ptB, lineAB, lineApt, dot_product, proj, dist)
    return dist

if __name__ == "__main__":

    config = {}
    config["seed"] = 0 #seed, 7 worked for initially sampling within term state subspace

    # Simple discrete environment usage example
    # config["state_space_type"] = "discrete"
    # config["action_space_type"] = "discrete"
    # config["state_space_size"] = 6
    # config["action_space_size"] = 6
    # config["reward_density"] = 0.25 # Number between 0 and 1
    # config["make_denser"] = True
    # config["terminal_state_density"] = 0.25 # Number between 0 and 1
    # config["completely_connected"] = True # Make every state reachable from every state
    # config["repeats_in_sequences"] = False
    # config["delay"] = 1
    # config["sequence_length"] = 3
    # config["reward_unit"] = 1.0
    # # config["transition_noise"] = 0.2 # Currently the fractional chance of transitioning to one of the remaining states when given the deterministic transition function - in future allow this to be given as function; keep in mind that the transition function itself could be made a stochastic function - does that qualify as noise though?
    # # config["reward_noise"] = lambda a: a.normal(0, 0.1) #random #hack # a probability function added to reward function

    # Simple continuous environment usage example
    # config["state_space_type"] = "continuous"
    # config["action_space_type"] = "continuous"
    # config["state_space_dim"] = 2
    # config["action_space_dim"] = 2
    # config["transition_dynamics_order"] = 1
    # config["inertia"] = 1 # 1 unit, e.g. kg for mass, or kg * m^2 for moment of inertia.
    # config["state_space_max"] = 5 # Will be a Box in the range [-max, max]
    # config["action_space_max"] = 1 # Will be a Box in the range [-max, max]
    # config["time_unit"] = 1 # Discretization of time domain
    # config["terminal_states"] = [[0.0, 1.0], [1.0, 0.0]]
    # config["term_state_edge"] =  1.0 # Terminal states will be in a hypercube centred around the terminal states given above with the edge of the hypercube of this length.
    #
    # config["delay"] = 1
    # config["sequence_length"] = 10
    # config["reward_scale"] = 1.0
    # # config["reward_noise"] = lambda a: a.normal(0, 0.1) #random #hack # a probability function added to reward function
    # # config["transition_noise"] = lambda a: a.normal(0, 0.1) #random #hack # a probability function added to transition function in cont. spaces
    # config["reward_function"] = "move_along_a_line"

    config["generate_random_mdp"] = True # This supersedes previous settings and generates a random transition function, a random reward function (for random specific sequences)
    env = RLToyEnv(config)
    state = copy.copy(env.get_augmented_state()['curr_state'])
    for _ in range(20):
        # env.render() # For GUI
        action = env.action_space.sample() # take a #random action
        # action = np.array([1, 1, 1, 1]) # just to test if acting "in a line" works
        next_state, reward, done, info = env.step(action)
        print("sars', done =", state, action, reward, next_state, done, "\n")
        state = copy.copy(next_state)
    env.reset()
    env.close()

    # import sys
    # sys.exit(0)
