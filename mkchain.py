import argparse
import json
import os
import subprocess
import sys
import yaml

from datetime import datetime
from datetime import timezone
from ipaddress import IPv4Address

my_path = os.path.dirname(os.path.abspath(__file__))


# https://stackoverflow.com/questions/25833613/python-safe-method-to-get-value-of-nested-dictionary/25833661
def safeget(dct, *keys):
    for key in keys:
        try:
            dct = dct[key]
        except KeyError:
            return None
    return dct


def run_docker(entrypoint, mount, *args, image="tezos/tezos:v7-release"):
    return subprocess.check_output(
        "docker run --entrypoint %s -u %s:%s --rm -v %s %s %s"
        % (entrypoint, os.getuid(), os.getgid(), mount, image, " ".join(args)),
        shell=True,
    )


def gen_key(key_dir, key_name):
    entrypoint = "/usr/local/bin/tezos-client"
    mount = key_dir + ":/data"
    run_docker(
        entrypoint,
        mount,
        "-d",
        "/data",
        "--protocol",
        "PsCARTHAGazK",
        "gen",
        "keys",
        key_name,
        "--force",
    )


def get_key(key_dir, key_name):
    entrypoint = "/usr/local/bin/tezos-client"
    mount = key_dir + ":/data"
    return run_docker(
        entrypoint,
        mount,
        "-d",
        "/data",
        "--protocol",
        "PsCARTHAGazK",
        "show",
        "address",
        key_name,
    ).split(b"\n")[1].split(b":")[1].strip().decode("utf-8")


# def get_key(key_dir, name):
#     with open(os.path.join(key_dir, "public_keys"), "r") as keyfile:
#         keys = json.load(keyfile)
#         offset = 0
#         for key in keys:
#             if key["name"] == name:
#                 value = key["value"]
#                 if value.startswith("unencrypted:"):
#                     offset = len("unencrypted:")
#                 return value[offset:]


def get_identity_job(docker_image):
    return [
        {
            "name": "identity-job",
            "image": docker_image,
            "command": ["/usr/local/bin/tezos-node"],
            "args": [
                "identity",
                "generate",
                "0",
                "--data-dir",
                "/var/tezos/node",
                "--config-file",
                "/etc/tezos/config.json",
            ],
            "volumeMounts": [
                {"name": "config-volume", "mountPath": "/etc/tezos"},
                {"name": "var-volume", "mountPath": "/var/tezos"},
            ],
        }
    ]


def get_baker(docker_image, baker_command):
    return {
        "name": "baker-job",
        "image": docker_image,
        "command": [baker_command],
        "args": [
            "-A",
            "tezos-rpc",
            "-P",
            "8732",
            "-d",
            "/var/tezos/client",
            "run",
            "with",
            "local",
            "node",
            "/var/tezos/node",
            "baker",
        ],
        "volumeMounts": [{"name": "var-volume", "mountPath": "/var/tezos"}],
    }


def get_endorser(docker_image, endorser_command):
    return {
        "name": "endorser",
        "image": docker_image,
        "command": [endorser_command],
        "args": [
            "-A",
            "tezos-rpc",
            "-P",
            "8732",
            "-d",
            "/var/tezos/client",
            "run",
            "baker",
        ],
        "volumeMounts": [{"name": "var-volume", "mountPath": "/var/tezos"}],
    }


# FIXME - this should probably be replaced with subprocess calls to tezos-node-config
def generate_node_config(node_argv):
    parser = argparse.ArgumentParser(prog="nodeconfig")
    subparsers = parser.add_subparsers(help="sub-command help", dest="subparser_name")

    global_parser = subparsers.add_parser("global")
    global_parser.add_argument("--data-dir", default="/var/tezos/node")

    rpc_parser = subparsers.add_parser("rpc")
    rpc_parser.add_argument("--listen-addrs", action="append", default=[":8732"])

    p2p_parser = subparsers.add_parser("p2p")
    p2p_parser.add_argument("--bootstrap-peers", action="append", default=[])
    p2p_parser.add_argument("--listen-addr", default="[::]:9732")
    p2p_parser.add_argument("--expected-proof-of-work", default=0, type=int)

    network_parser = subparsers.add_parser("network")
    network_parser.add_argument("--chain-name")
    network_parser.add_argument("--sandboxed-chain-name", default="SANDBOXED_TEZOS")
    network_parser.add_argument(
        "--default-bootstrap-peers", action="append", default=[]
    )

    genesis_parser = subparsers.add_parser("genesis")
    genesis_parser.add_argument("--timestamp")
    genesis_parser.add_argument(
        "--block", default="BLockGenesisGenesisGenesisGenesisGenesisd6f5afWyME7"
    )
    genesis_parser.add_argument(
        "--protocol", default="PtYuensgYBb3G3x1hLLbCmcav8ue8Kyd2khADcL5LsT5R1hcXex"
    )

    genesis_parameters_parser = subparsers.add_parser("genesis_parameters")
    genesis_parameters_parser.add_argument("--genesis-pubkey")

    namespaces = []
    while node_argv:
        namespace, node_argv = parser.parse_known_args(node_argv)
        namespaces.append(namespace)
        if not namespace.subparser_name:
            break

    node_config = {}
    special_keys = [
        "listen_addrs",
        "bootstrap_peers",
        "data_dir",
        "listen_addr",
        "expected_proof_of_work",
    ]
    for namespace in namespaces:
        section = vars(namespace)
        fixed_section = {}
        for k, v in section.items():
            if k in special_keys:
                fixed_section[k.replace("_", "-")] = v
            else:
                fixed_section[k] = v

        key = fixed_section.pop("subparser_name")
        if key == "global":
            node_config.update(fixed_section)
        else:
            # doubly nested parsers are a bit tricky. we'll just force the network keys where they belong
            if key == "genesis":
                node_config["network"][key] = fixed_section
            elif key == "genesis_parameters":
                node_config["network"][key] = {"values": fixed_section}
            else:
                node_config[key] = fixed_section

    return node_config


def generate_parameters_config(parameters_argv):
    parser = argparse.ArgumentParser(prog="parametersconfig")
    parser.add_argument(
        "--bootstrap-accounts",
        type=str,
        nargs="+",
        action="append",
        help="public key, mutez",
    )
    parser.add_argument("--preserved-cycles", type=int, default=2)
    parser.add_argument("--blocks-per-cycle", type=int, default=8)
    parser.add_argument("--blocks-per-commitment", type=int, default=4)
    parser.add_argument("--blocks-per-roll-snapshot", type=int, default=4)
    parser.add_argument("--blocks-per-voting-period", type=int, default=64)
    parser.add_argument("--time-between-blocks", default=["10", "20"])
    parser.add_argument("--endorsers-per-block", type=int, default=32)
    parser.add_argument("--hard-gas-limit-per-operation", default="800000")
    parser.add_argument("--hard-gas-limit-per-block", default="8000000")
    parser.add_argument("--proof-of-work-threshold", default="0")
    parser.add_argument("--tokens-per-roll", default="8000000000")
    parser.add_argument("--michelson-maximum-type-size", type=int, default=1000)
    parser.add_argument("--seed-nonce-revelation-tip", default="125000")
    parser.add_argument("--origination-size", type=int, default=257)
    parser.add_argument("--block-security-deposit", default="512000000")
    parser.add_argument("--endorsement-security-deposit", default="64000000")
    parser.add_argument("--endorsement-reward", default=["2000000"])
    parser.add_argument("--cost-per-byte", default="1000")
    parser.add_argument("--hard-storage-limit-per-operation", default="60000")
    parser.add_argument("--test-chain-duration", default="1966080")
    parser.add_argument("--quorum-min", type=int, default=2000)
    parser.add_argument("--quorum-max", type=int, default=7000)
    parser.add_argument("--min-proposal-quorum", type=int, default=500)
    parser.add_argument("--initial-endorsers", type=int, default=1)
    parser.add_argument("--delay-per-missing-endorsement", default="1")
    parser.add_argument("--baking-reward-per-endorsement", default=["200000"])

    namespace = parser.parse_args(parameters_argv)
    return vars(namespace)


def get_node_config(chain_name, genesis_key, timestamp, bootstrap_peers):

    p2p = ["p2p"]
    for bootstrap_peer in bootstrap_peers:
        p2p.extend(["--bootstrap-peers", bootstrap_peer])

    node_config_args = p2p + [
        "global",
        "rpc",
        "network",
        "--chain-name",
        chain_name,
        "genesis",
        "--timestamp",
        timestamp,
        "genesis_parameters",
        "--genesis-pubkey",
        genesis_key,
    ]

    return generate_node_config(node_config_args)


def get_parameters_config(key_dir, bootstrap_accounts, bootstrap_mutez):
    parameter_config_argv = []
    for account in bootstrap_accounts:
        parameter_config_argv.extend(
            ["--bootstrap-accounts", get_key(key_dir, account), bootstrap_mutez]
        )
    return generate_parameters_config(parameter_config_argv)


def get_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("chain_name")

    parser.add_argument("--tezos-dir", default=os.path.expanduser("~/.tq/"))
    parser.add_argument("--baker", action="store_true")
    parser.add_argument("--docker-image", default="tezos/tezos:v7-release")
    parser.add_argument("--bootstrap-mutez", default="4000000000000")

    group = parser.add_mutually_exclusive_group()
    group.add_argument("--create", action="store_true", help="Create a private chain")
    group.add_argument("--join", action="store_true", help="Join a private chain")

    subparsers = parser.add_subparsers(help="clusters")

    parser.add_argument("--bootstrap_peer", help="peer ip to join")
    parser.add_argument(
        "--genesis_key", help="genesis public key for the chain to join"
    )
    parser.add_argument("--timestamp", help="timestamp for the chain to join")

    parser.add_argument("--stdout", action="store_true")
    parser.add_argument(
        "--protocol-hash", default="PsCARTHAGazKbHtnKfLzQg3kms52kSRpgnDY982a9oYsSXRLQEb"
    )
    parser.add_argument("--baker-command", default="tezos-baker-006-PsCARTHA")

    # add a parser for each cluster type we want to support
    parser_minikube = subparsers.add_parser(
        "minikube", help="generate config for minikube"
    )
    parser_minikube.set_defaults(minikube=True)

    parser_eks = subparsers.add_parser("eks", help="generate config for EKS")
    parser_eks.set_defaults(eks=True)
    parser_eks.add_argument("gdb_volume_id")
    parser_eks.add_argument("gdb_aws_region")

    parser_kind = subparsers.add_parser("kind", help="generate config for kind")
    parser_kind.set_defaults(kind=True)

    parser_docker_desktop = subparsers.add_parser(
        "docker-desktop", help="generate config for docker-desktop"
    )
    parser_docker_desktop.set_defaults(docker_desktop=True)

    return parser.parse_args()


def main():
    args = get_args()
    tokens = {}

    key_dir = os.path.join(args.tezos_dir, "client")
    os.makedirs(key_dir, exist_ok=True)
    node_dir = os.path.join(args.tezos_dir, "node")
    tokens["node_dir"] = node_dir
    os.makedirs(node_dir, exist_ok=True)

    genesis_key = None
    timestamp = datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()
    bootstrap_peers = []
    bootstrap_accounts = ["baker", "bootstrap_account_1", "bootstrap_account_2"]
    k8s_templates = ["common.yaml", "pv.yaml"]

    if args.create:
        k8s_templates.append("activate.yaml")
        for account in bootstrap_accounts + ["genesis"]:
            gen_key(key_dir, account)
        genesis_key = get_key(key_dir, "genesis")
        bootstrap_peers = []

    if args.join:
        genesis_key = args.genesis_key
        # validate peer ip
        bootstrap_peers = [str(IPv4Address(args.bootstrap_peer))]
        timestamp = args.timestamp

    if genesis_key is None:
        genesis_key = get_key(key_dir, "genesis")

    minikube_gw = None
    if "minikube" in args:
        try:
            minikube_route = (
                subprocess.check_output(
                    '''minikube ssh "ip route show default"''', shell=True
                )
                .decode("utf-8")
                .split()
            )
            minikube_gw, minikube_iface = minikube_route[2], minikube_route[4]
            minikube_ip = (
                subprocess.check_output(
                    '''minikube ssh "ip addr show %s|awk /^[[:space:]]+inet/'{print \$2}'"'''
                    % minikube_iface,
                    shell=True,
                )
                .decode("utf-8")
                .split("/")[0]
            )
            print("Add the following line to /etc/exports and reload nfsd.")
            print(
                '"%s" -alldirs -mapall=%s:%s %s'
                % (args.tezos_dir, os.getuid(), os.getgid(), minikube_ip)
            )
        except subprocess.CalledProcessError as e:
            print("failed to get minikube route %r" % e)

    k8s_objects = []
    for template in k8s_templates:
        with open(os.path.join(my_path, "deployment", template), "r") as yaml_template:

            k8s_resources = yaml.load_all(yaml_template, Loader=yaml.FullLoader)
            for k in k8s_resources:
                if (
                    safeget(k, "kind") == "PersistentVolume"
                    and safeget(k, "metadata", "name") == "tezos-var-volume"
                ):
                    if "docker_desktop" in args:
                        k["spec"]["hostPath"] = {"path": args.tezos_dir}
                    elif "minikube" in args:
                        k["spec"]["nfs"] = {
                            "path": args.tezos_dir,
                            "server": minikube_gw,
                        }

                if safeget(k, "metadata", "name") == "tezos-config":
                    k["data"] = {
                        "parameters.json": json.dumps(
                            get_parameters_config(
                                key_dir, bootstrap_accounts, args.bootstrap_mutez
                            )
                        ),
                        "config.json": json.dumps(
                            get_node_config(
                                args.chain_name, genesis_key, timestamp, bootstrap_peers
                            )
                        ),
                    }

                if safeget(k, "metadata", "name") == "tezos-node":
                    # set the docker image for the node
                    k["spec"]["template"]["spec"]["containers"][0][
                        "image"
                    ] = args.docker_image

                    if not os.path.isfile(
                        os.path.join(args.tezos_dir, "node", "identity.json")
                    ):
                        # add the identity job
                        k["spec"]["template"]["spec"][
                            "initContainers"
                        ] = get_identity_job(args.docker_image)
                    if args.baker:
                        k["spec"]["template"]["spec"]["containers"].append(
                            get_baker(args.docker_image, args.baker_command)
                        )

                if safeget(k, "metadata", "name") == "activate-job":
                    k["spec"]["template"]["spec"]["initContainers"][1][
                        "image"
                    ] = args.docker_image
                    k["spec"]["template"]["spec"]["initContainers"][1]["args"] = [
                        "-A",
                        "tezos-rpc",
                        "-P",
                        "8732",
                        "-d",
                        "/var/tezos/client",
                        "-l",
                        "--block",
                        "genesis",
                        "activate",
                        "protocol",
                        args.protocol_hash,
                        "with",
                        "fitness",
                        "-1",
                        "and",
                        "key",
                        "genesis",
                        "and",
                        "parameters",
                        "/etc/tezos/parameters.json",
                    ]
                    k["spec"]["template"]["spec"]["initContainers"][2][
                        "image"
                    ] = args.docker_image

                k8s_objects.append(k)

    if args.stdout:
        out = sys.stdout
    else:
        out = open(f"{args.chain_name}.yaml", "w")

    yaml.dump_all(k8s_objects, out)


if __name__ == "__main__":
    main()