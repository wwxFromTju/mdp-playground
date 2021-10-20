"""Script to run experiments on MDP Playground.

Takes a configuration file, experiment name and config number to run as
optional arguments.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import argparse
import mdp_playground.config_processor as config_processor

import os
import logging
import cloudpickle

import ray
from ray import tune
from ray.rllib.agents.callbacks import MemoryTrackingCallbacks

# import configparser
import pprint

pp = pprint.PrettyPrinter(indent=4)


def generate_parser():
    """
    Helper function generating the argparser for the main function.

    Returns
    -------
    argparse.ArgumentParser
    """
    # docstring at beginning of the file is stored in __doc__
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-c",
        "--config-file",
        dest="config_file",
        action="store",
        default="default_config",
        help="Configuration file containing configuration to run "
        "experiments. It must be a Python file so config can be "
        "given programmatically. There are 2 types of configs - "
        "VARIABLE CONFIG across the experiments and STATIC CONFIG "
        "across the experiments. \nVARIABLE CONFIGS: The "
        "OrderedDicts var_env_configs, var_agent_configs and "
        "var_model_configs hold configuration options that are "
        "variable for the environment, agent and model across the "
        "current experiment. For each configuration option, the "
        "option is the key in the dict and its value is a list of "
        "values it can take for the current experiment. A "
        "Cartesian product of these lists is taken to generate "
        "various possible configurations to be run. For example, "
        'you might want to vary "delay" for the current '
        'experiment. Then "delay" would be a key in '
        "var_env_configs dict and its value would be a list of "
        "values it can take. Because Ray does not have a common "
        "way to address this specification of configurations for "
        "its agents, there are a few hacky ways to set "
        "var_agent_configs and var_model_configs currently. "
        "Please see sample experiment config files in the "
        "experiments directory to see how to set the values for a "
        "given algorithm. STATIC CONFIGS: env_config, "
        "agent_config and model_config are dicts which hold the "
        "static configuration for the current experiment as a "
        "normal Python dict.",
    )
    # ####TODO Update docs regarding how to get configs to run: i.e., Cartesian
    # product, or random, etc.
    parser.add_argument(
        "-e",
        "--exp-name",
        dest="exp_name",
        action="store",
        default="mdpp_default_experiment",
        help="The user-chosen name of the experiment. This is used"
        " as the prefix of the output files (the prefix also "
        "contains config_num if that is provided). It will save "
        "stats to 2 CSV files, with the filenames as the one given"
        " as argument"
        ' and another file with an extra "_eval" in the filename '
        "that contains evaluation stats during the training. "
        "Appends to existing files or creates new ones if they "
        "don't exist.",
    )
    parser.add_argument(
        "-n",
        "--config-num",
        dest="config_num",
        action="store",
        default=None,
        type=int,
        help="Used for running the configurations of experiments "
        "in parallel. This is appended to the prefix of the output"
        " files (after exp_name)."
        " A Cartesian product of different configuration values "
        "for the experiment will be taken and ordered as a list "
        "and this number corresponds to the configuration number "
        "in this list. Please look in to the code for details.",
    )
    # ###TODO Remove? #hack to run 1000 x 1000 env configs x agent configs.
    # Storing all million of them in memory may be too inefficient?
    parser.add_argument(
        "-a",
        "--agent-config-num",
        dest="agent_config_num",
        action="store",
        default=None,
        type=int,
        help="Used for running the configurations of experiments "
        "in parallel. This is appended to the prefix of the output"
        " files (after exp_name).",
    )
    parser.add_argument(
        "-f",
        "--framework",
        dest="framework",
        action="store",
        default="ray",
        type=str,
        help="Specify framework to run "
        "experiments (Current options: Ray Rllib, Stable Baselines"
        ").",
    )
    parser.add_argument(
        "-m",
        "--save-model",
        dest="save_model",
        action="store",
        default=False,
        type=bool,
        help="Option to save trained NN model and framework \
                        generated files at the end of "
        "training.",
    )
    parser.add_argument(
        "-t",
        "--framework-dir",
        dest="framework_dir",
        action="store",
        default="/tmp/",
        type=str,
        help="Prefix of directory to be used by underlying "
        "framework (e.g. Ray Rllib, Stable Baselines 3). This "
        "name will be passed to the framework.",
    )
    # parser.add_argument('-t', '--tune-hps', dest='tune_hps', action='store',
    #                     default=False, type=bool,
    #                     help='Used for tuning the hyperparameters that can be '
    #                     'used for experiments later.'
    #                     ' A Cartesian product of different configuration values '
    #                     'for the experiment will be taken and ordered as a list '
    #                     'and this number corresponds to the configuration number'
    #                     ' in this list.'
    #                     ' Please look in to the code for details.')
    parser.add_argument("-l", "--log-level",
                        default="WARNING", help="Set log level.")
    parser.add_argument("--local-mode", action='store_true',
                        default=False,
                        help='Initialize ray in local mode.')
    parser.add_argument('-k', '--num_cpus', default=1,
                        help="Num CPU cores for Ray to use.")
    parser.add_argument('-o', '--object_store_memory', default=1e9,
                        help="Size (in bytes) to use for Ray's object store.")
    parser.add_argument("-w", "--wandb", default=None,
                        help="Use Tune's wandb callback for tracking the exp."
                        "Passed arg is the wand project name. No syncing if "
                        "none provided. Only works for Ray >= 1.0.")
    parser.add_argument("-r", "--ray_init", action='store_false',
                        default=True,
                        help="Flag to DEACTIVATE Ray initialization in case of"
                        " initialization of Ray outside MDPP.")

    return parser


def main(args):
    """
    Main function for running an experiment.

    Parameters
    ----------
    args : list[str]
        Command line arguments.

    Returns
    -------
    list[str], str
        The list of names of the pickled Tune analysis files (one per config)
        and the name of the stats file generated by MDPP (to be used with MDPP
        analysis).
    """
    # #TODO Different seeds for Ray Trainer (TF, numpy, Python; Torch, Env),
    # Environment (it has multiple sources of randomness too), Ray Evaluator
    parser = generate_parser()
    args = parser.parse_args(args)
    print("Parsed arguments:", args)

    log_levels = {
        "CRITICIAL": logging.CRITICAL,
        "ERROR": logging.ERROR,
        "WARNING": logging.WARNING,
        "INFO": logging.INFO,
        "DEBUG": logging.DEBUG,
        "NOTSET": logging.NOTSET,
    }

    try:
        log_level_ = log_levels[args.log_level]
    except ValueError:
        logging.error(
            "Log level {} not in {}.".format(args.log_level, log_levels.keys())
        )

    config_file = args.config_file

    if args.config_file[-3:] == ".py":
        config_file = args.config_file[:-3]

    # print("config_file_path:", config_file_path)

    stats_file_name = os.path.abspath(args.exp_name)

    if args.config_num is not None:
        stats_file_name += "_" + str(args.config_num)
    # elif args.agent_config_num is not None: ###IMP Commented out! If we append
    # both these nums then, that can lead to 1M small files for 1000x1000 configs
    # which doesn't play well with our Nemo cluster.
    #     stats_file_name += '_' + str(args.agent_config_num)

    print("Stats file being written to:", stats_file_name)

    ray_kwargs = {
        'local_mode': args.local_mode,
        'num_cpus': int(args.num_cpus),
        'object_store_memory': int(float(args.object_store_memory)),
    }

    config, final_configs = config_processor.process_configs(
        config_file,
        stats_file_prefix=stats_file_name,
        framework=args.framework,
        config_num=args.config_num,
        log_level=log_level_,
        framework_dir=args.framework_dir,
        init_ray=args.ray_init,
        **ray_kwargs
    )

    print(
        "Configuration number(s) that will be run:",
        "all" if args.config_num is None else args.config_num,
    )

    # import default_config
    # print("default_config:", default_config)
    # print(os.path.abspath(args.config_file)) # 'experiments/dqn_seq_del.py'

    import time

    start = time.time()

    if args.config_num is None:
        # final_configs = config.final_configs
        print("Total number of configs to run:", len(final_configs))
        pass
    else:
        final_configs = [final_configs[args.config_num]]

    analysis_files = []

    for enum_conf_1, current_config_ in enumerate(final_configs):
        print("current_config of agent to be run:",
              current_config_, enum_conf_1)

        algorithm = config.algorithm

        tune_config = current_config_
        print(
            "tune_config:",
        )
        pp.pprint(tune_config)

        if "timesteps_total" in dir(config):
            timesteps_total = config.timesteps_total
        else:
            timesteps_total = tune_config["timesteps_total"]

        # hack Ray doesn't allow unknown configs
        del tune_config["timesteps_total"]

        print(
            "\n\033[1;32m======== Running on environment: "
            + tune_config["env"]
            + " =========\033[0;0m\n"
        )
        print(
            "\n\033[1;32m======== for "
            + str(timesteps_total)
            + " steps =========\033[0;0m\n"
        )

        res_dir = args.framework_dir + "/_ray_results_" + str(args.config_num)
        print("## Results dir: {}".format(res_dir))

        callbacks = [MemoryTrackingCallbacks]

        if args.wandb is not None:
            from ray.tune.integration.wandb import WandbLoggerCallback
            API_KEY_FILE = "~/wandb_api_key.txt"
            callbacks.append(WandbLoggerCallback(
                project=args.wandb,
                api_key_file=API_KEY_FILE,
                log_config=True))

        if ray.__version__[0] == "0":
            analysis = tune.run(
                algorithm,
                name=algorithm
                + "_"
                + str(stats_file_name.split("/")[-1])
                + "_",  # IMP "name" has to be specified, otherwise,
                # it may lead to clashing for temp file in ~/ray_results/... directory.
                stop={
                    "timesteps_total": timesteps_total,
                },
                config=tune_config,
                checkpoint_at_end=args.save_model,
                local_dir=res_dir,
            )
        else:
            analysis = tune.run(
                algorithm,
                name=algorithm
                + "_"
                + str(stats_file_name.split("/")[-1])
                + "_",  # IMP "name" has to be specified, otherwise,
                # it may lead to clashing for temp file in ~/ray_results/... directory.
                stop={
                    "timesteps_total": timesteps_total,
                },
                config=tune_config,
                callbacks=callbacks,
                checkpoint_at_end=args.save_model,
                local_dir=res_dir,
            )

        if args.save_model:
            analysis_file_name = "{}_{}_analysis.pickle".format(
                args.exp_name, enum_conf_1)
            cloudpickle.dump(
                analysis, open(
                    analysis_file_name,
                    "wb")
            )
            analysis_files.append(analysis_file_name)

    config_processor.post_processing(framework=args.framework)

    end = time.time()
    print("No. of seconds to run:", end - start)

    return analysis_files, stats_file_name


def cli():
    """
    Helper function to call main from CLI.
    """
    import sys

    main(sys.argv[1:])


if __name__ == "__main__":
    cli()
