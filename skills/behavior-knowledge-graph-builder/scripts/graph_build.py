#!/usr/bin/env python3
"""Validate draft behavior graphs and package a result for an immutable build request."""

import argparse
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True

from graph_schema import GraphError, read_graph
from build_contract import make_result, read_packet, validate_request, write_new_packet


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("--graph", required=True, type=Path)
    result = commands.add_parser("result")
    result.add_argument("--request", required=True, type=Path)
    result.add_argument("--graph", required=True, type=Path)
    result.add_argument("--report", required=True, type=Path)
    result.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        graph = read_graph(args.graph)
        if args.command == "validate":
            output = {"valid": True, "nodes": len(graph["nodes"]), "dependencies": len(graph["dependencies"]),
                      "notice": "Structure and reference integrity checked; evidence truth and completeness are not verified."}
        else:
            request = validate_request(read_packet(args.request))
            packet = make_result(request, graph, read_packet(args.report))
            path = write_new_packet(args.out, packet)
            output = {"result": str(path), "request_id": packet["request_id"], "graph_digest": packet["graph_digest"],
                      "changes": packet["changes"], "notice": "Draft result saved. No review repository, processing receipt, approval, or final export was changed."}
        print(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except GraphError as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 1
    except OSError:
        print(json.dumps({"error": "Cannot save the graph build artifact."}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
