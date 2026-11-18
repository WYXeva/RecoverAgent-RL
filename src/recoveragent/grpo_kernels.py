"""Inspect the pinned Transformers dispatch; optional small CUDA kernel smoke."""

import inspect
from importlib.metadata import PackageNotFoundError, version


def implementation(function):
    # The pinned Transformers decorator resolves its backend at module import.
    # Checking package installation alone misses failed CUDA-extension imports.
    while function is not None:
        closure = inspect.getclosurevars(function).nonlocals
        if "is_new_implementation" in closure:
            backend = closure["implementation"]
            return {
                "optimized": closure["is_new_implementation"],
                "implementation": backend.__module__ + "." + backend.__name__,
            }
        function = getattr(function, "__wrapped__", None)
    return {"optimized": False, "implementation": "unrecognized_dispatch"}


def inspect_kernels(*, required=False):
    from transformers.models.qwen3_5 import modeling_qwen3_5 as qwen

    packages = {}
    for name in ("torch", "transformers", "flash-linear-attention", "causal-conv1d"):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = None
    dispatch = {
        name: implementation(getattr(qwen, name))
        for name in ("causal_conv1d_fn", "torch_chunk_gated_delta_rule")
    }
    result = {"packages": packages, "dispatch": dispatch}
    result["optimized"] = all(r["optimized"] for r in dispatch.values())
    if required and not result["optimized"]:
        raise ValueError(
            "Optimized Qwen3.5 training kernels unavailable: " + str(result)
        )
    return result


def smoke():
    """Synthetic 128-token BF16 forward/backward through the actual dispatch."""
    import time

    import torch
    from transformers.models.qwen3_5 import modeling_qwen3_5 as qwen

    result = inspect_kernels(required=True)
    if not torch.cuda.is_available():
        raise ValueError("CUDA device required for kernel smoke")
    torch.manual_seed(42)
    started = time.monotonic()

    def random(*shape):
        return torch.randn(
            *shape, device="cuda", dtype=torch.bfloat16, requires_grad=True
        )

    x, weight = random(1, 64, 128), random(64, 4)
    conv = qwen.causal_conv1d_fn(x, weight, activation="silu")
    q, k, v = [random(1, 128, 2, 64) for _ in range(3)]
    g = torch.full((1, 128, 2), -0.1, device="cuda", requires_grad=True)
    beta = torch.full((1, 128, 2), 0.5, device="cuda", requires_grad=True)
    delta, _ = qwen.torch_chunk_gated_delta_rule(
        q, k, v, g=g, beta=beta, use_qk_l2norm_in_kernel=True
    )
    loss = conv.float().square().mean() + delta.float().square().mean()
    loss.backward()
    torch.cuda.synchronize()
    parameters = (x, weight, q, k, v, g, beta)
    finite = bool(torch.isfinite(loss)) and all(
        p.grad is not None and bool(torch.isfinite(p.grad).all()) for p in parameters
    )
    nonzero = all(float(p.grad.float().norm()) > 0 for p in parameters)
    if not finite or not nonzero:
        raise ValueError("Kernel smoke gradient check failed")
    return {
        **result,
        "synthetic_cuda_smoke": True,
        "finite_nonzero_gradients": True,
        "seconds_including_jit": time.monotonic() - started,
        "gpu": torch.cuda.get_device_name(),
    }
