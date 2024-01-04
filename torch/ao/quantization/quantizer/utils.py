import functools
from typing import Any, Callable, List, Optional

import torch
import torch.nn.functional as F

from torch.ao.quantization.pt2e.utils import (
    _conv2d_bn_example_inputs,
    _conv2d_example_inputs,
    _is_sym_size_node,
    get_aten_graph_module,
)
from torch.ao.quantization.quantizer.quantizer import QuantizationAnnotation
from torch.fx import Node
from torch.fx.passes.utils.matcher_with_name_node_map_utils import (
    SubgraphMatcherWithNameNodeMap,
)


__all__ = []  # type: ignore[var-annotated]


def _annotate_input_qspec_map(node: Node, input_node: Node, qspec):
    quantization_annotation = node.meta.get(
        "quantization_annotation", QuantizationAnnotation()
    )
    if quantization_annotation.input_qspec_map is None:
        quantization_annotation.input_qspec_map = {}
    quantization_annotation.input_qspec_map[input_node] = qspec
    node.meta["quantization_annotation"] = quantization_annotation


def _annotate_output_qspec(node: Node, qspec):
    quantization_annotation = node.meta.get(
        "quantization_annotation", QuantizationAnnotation()
    )
    quantization_annotation.output_qspec = qspec
    node.meta["quantization_annotation"] = quantization_annotation


def _node_only_used_for_sym_size(node: Node, partition_nodes: List[Node]):
    """
    This utility is used to handle cases when dynami_shape=True tracing leads
    to symint nodes in the pattern of linear module. In those cases, we need to
    distinguish between the nodes that are in input for just extracting value of
    some dimentions (and symint nodes) vs. the one that is activation.
    For example:
    graph(x, y, weight):
       size_0 = torch.ops.aten.sym_size([x], [0])
       size_1 = torch.ops.aten.sym_size([y], [1])
       view_size = size_0 * size_1
       size_3 = torch.ops.aten.sym_size([x], [2])
       vie_out = torch.ops.aten.view(x, [view_size, size_3])
       return mm(view_out, weight)
    In the example above y node is not actual input. It exist only to extract size_1
    """
    if _is_sym_size_node(node):
        return True

    return all(
        ((user not in partition_nodes) or _is_sym_size_node(user))
        for user in node.users
    )


def _get_conv_unary_pattern(
    conv_fn: Callable,
    has_bn: bool = False,  # Usually need for QAT pattern
    unary_fn: Optional[Callable[[Any], Any]] = None,
) -> Callable:
    # A helper function to generate a Conv Unary pattern,
    # which serves as an input to create a SubgraphMatcherWithNameNodeMap.
    def _conv_unary(
        x,
        conv_weight,
        conv_bias,
        bn_weight=None,
        bn_bias=None,
        bn_rm=None,
        bn_rv=None,
    ):
        conv = conv_fn(x, conv_weight, conv_bias)
        if has_bn:
            bn = F.batch_norm(conv, bn_rm, bn_rv, bn_weight, bn_bias, training=True)
        else:
            bn = conv
        if unary_fn is not None:
            unary = unary_fn(bn)
            output = unary
        else:
            unary = bn
            output = unary
        return output, {
            "input": x,
            "conv": conv,
            "conv_weight": conv_weight,
            "conv_bias": conv_bias,
            "bn": bn,
            "unary": unary,
            "output": output,
        }

    return _conv_unary


def _generate_pattern_matcher_helper(
    pattern, example_inputs
) -> SubgraphMatcherWithNameNodeMap:
    pattern = get_aten_graph_module(pattern, example_inputs)
    pattern.graph.eliminate_dead_code()
    pattern.recompile()
    # print("qat pattern is: {}".format(pattern.graph), flush=True)
    return SubgraphMatcherWithNameNodeMap(pattern, ignore_literals=True)


@functools.lru_cache(None)
def _generate_conv2d_pattern_matcher() -> SubgraphMatcherWithNameNodeMap:
    # Ensure it's only be invoked once, due to the cache size limitation in
    # https://github.com/pytorch/pytorch/blob/
    # 4c6e842496da636123f83ef868ca1974631f1f1e/torch/_dynamo/config.py#L35-L39
    return _generate_pattern_matcher_helper(
        _get_conv_unary_pattern(torch.nn.functional.conv2d),
        _conv2d_example_inputs,
    )


@functools.lru_cache(None)
def _generate_conv2d_bn_pattern_matcher():
    return _generate_pattern_matcher_helper(
        _get_conv_unary_pattern(torch.nn.functional.conv2d, has_bn=True),
        _conv2d_bn_example_inputs,
    )


@functools.lru_cache(None)
def _generate_conv2d_relu_pattern_matcher():
    return _generate_pattern_matcher_helper(
        _get_conv_unary_pattern(
            torch.nn.functional.conv2d, unary_fn=torch.nn.functional.relu
        ),
        _conv2d_example_inputs,
    )


@functools.lru_cache(None)
def _generate_conv2d_inplace_relu_pattern_matcher():
    return _generate_pattern_matcher_helper(
        _get_conv_unary_pattern(
            torch.nn.functional.conv2d, unary_fn=torch.nn.functional.relu_
        ),
        _conv2d_example_inputs,
    )


@functools.lru_cache(None)
def _generate_conv2d_hardtanh_pattern_matcher():
    return _generate_pattern_matcher_helper(
        _get_conv_unary_pattern(
            torch.nn.functional.conv2d, unary_fn=torch.nn.functional.hardtanh
        ),
        _conv2d_example_inputs,
    )


@functools.lru_cache(None)
def _generate_conv2d_inplace_hardtanh_pattern_matcher():
    return _generate_pattern_matcher_helper(
        _get_conv_unary_pattern(
            torch.nn.functional.conv2d, unary_fn=torch.nn.functional.hardtanh_
        ),
        _conv2d_example_inputs,
    )


@functools.lru_cache(None)
def _generate_conv2d_relu6_pattern_matcher():
    return _generate_pattern_matcher_helper(
        _get_conv_unary_pattern(
            torch.nn.functional.conv2d, unary_fn=torch.nn.functional.relu6
        ),
        _conv2d_example_inputs,
    )


@functools.lru_cache(None)
def _generate_conv2d_inplace_relu6_pattern_matcher():
    return _generate_pattern_matcher_helper(
        _get_conv_unary_pattern(
            torch.nn.functional.conv2d, unary_fn=torch.nn.ReLU6(inplace=True)
        ),
        _conv2d_example_inputs,
    )
