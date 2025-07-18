# Copyright © 2023 Apple Inc.

import inspect
import math
import unittest
from functools import partial

import mlx.core as mx
import mlx.nn as nn
import mlx.optimizers as opt
import mlx.utils
import mlx_tests
import numpy as np
from mlx.utils import tree_flatten, tree_map, tree_unflatten

try:
    import torch
    import torch.nn.functional as F

    has_torch = True
except ImportError as e:
    has_torch = False


def get_all_optimizers():
    classes = dict()
    for name, obj in inspect.getmembers(opt):
        if (
            inspect.isclass(obj)
            and issubclass(obj, opt.Optimizer)
            and obj != opt.Optimizer
        ):
            classes[name] = obj
    return classes


def tree_equal(fn, *args):
    return all(v for _, v in tree_flatten(tree_map(fn, *args)))


optimizers_dict = get_all_optimizers()
del optimizers_dict["MultiOptimizer"]


class TestOptimizers(mlx_tests.MLXTestCase):
    def test_optimizer_state(self):
        optim = opt.SGD(0.1)
        optim.state["hello"] = "world"
        self.assertEqual(optim.state["hello"], "world")

        optim.state = {0: 1}
        self.assertEqual(optim.state, {0: 1})

    def test_optimizers(self):
        params = {
            "first": [mx.zeros((10,)), mx.zeros((1,))],
            "second": mx.zeros((1,)),
        }
        grads = tree_map(lambda x: mx.ones_like(x), params)

        for optim_class in optimizers_dict.values():
            optim = optim_class(0.1)
            update = optim.apply_gradients(grads, params)
            mx.eval(update)
            equal_shape = tree_map(lambda x, y: x.shape == y.shape, params, update)
            all_equal = all(v for _, v in mlx.utils.tree_flatten(equal_shape))
            self.assertTrue(all_equal)

    def test_types_conserved(self):
        params = {"w": mx.ones((5, 5), mx.float16)}
        grads = tree_map(lambda x: mx.ones_like(x), params)
        for optim_class in optimizers_dict.values():
            optim = optim_class(0.1)
            update = optim.apply_gradients(grads, params)
            self.assertEqual(update["w"].dtype, mx.float16)

    def test_sgd(self):
        params = {
            "first": [mx.zeros((10,)), mx.zeros((1,))],
            "second": mx.zeros((1,)),
        }
        grads = tree_map(lambda x: mx.ones_like(x), params)

        # Explicit init
        optim = opt.SGD(learning_rate=1e-2, momentum=0.9)
        optim.init(params)
        self.assertTrue(
            tree_equal(
                lambda p, s: mx.array_equal(s["v"], mx.zeros_like(p)),
                params,
                optim.state,
            )
        )

        # Implicit init
        optim = opt.SGD(learning_rate=1e-2, momentum=0.9)
        optim.apply_gradients(grads, params)
        self.assertTrue(
            tree_equal(lambda g, s: mx.array_equal(s["v"], g), grads, optim.state)
        )

    def test_rmsprop(self):
        params = {
            "first": [mx.zeros((10,)), mx.zeros((1,))],
            "second": mx.zeros((1,)),
        }
        grads = tree_map(lambda x: mx.ones_like(x), params)

        # Explicit init
        optim = opt.RMSprop(learning_rate=1e-2)
        optim.init(params)
        self.assertTrue(
            tree_equal(
                lambda p, s: mx.array_equal(s["v"], mx.zeros_like(p)),
                params,
                optim.state,
            )
        )

        # Implicit init
        alpha = 0.99
        optim = opt.RMSprop(learning_rate=1e-2, alpha=alpha)
        optim.apply_gradients(grads, params)
        self.assertTrue(
            tree_equal(
                lambda g, s: mx.allclose(s["v"], (1 - alpha) * g), grads, optim.state
            )
        )

    def test_adagrad(self):
        params = {
            "first": [mx.zeros((10,)), mx.zeros((1,))],
            "second": mx.zeros((1,)),
        }
        grads = tree_map(lambda x: mx.ones_like(x), params)

        # Explicit init
        optim = opt.Adagrad(learning_rate=1e-2)
        optim.init(params)
        self.assertTrue(
            tree_equal(
                lambda p, s: mx.array_equal(s["v"], mx.zeros_like(p)),
                params,
                optim.state,
            )
        )

    def test_adadelta(self):
        params = {
            "first": [mx.zeros((10,)), mx.zeros((1,))],
            "second": mx.zeros((1,)),
        }
        grads = tree_map(lambda x: mx.ones_like(x), params)

        # Explicit init
        optim = opt.AdaDelta(learning_rate=1e-2)
        optim.init(params)
        self.assertTrue(
            tree_equal(
                lambda p, s: mx.array_equal(s["v"], mx.zeros_like(p)),
                params,
                optim.state,
            )
        )
        self.assertTrue(
            tree_equal(
                lambda p, s: mx.array_equal(s["u"], mx.zeros_like(p)),
                params,
                optim.state,
            )
        )

    def test_adam(self):
        params = {
            "first": [mx.zeros((10,)), mx.zeros((1,))],
            "second": mx.zeros((1,)),
        }
        grads = tree_map(lambda x: mx.ones_like(x), params)

        # Explicit init
        for optimizer in [opt.Adam, opt.AdamW, opt.Adamax]:
            optim = optimizer(learning_rate=1e-2)
            optim.init(params)
            self.assertTrue(
                tree_equal(
                    lambda p, s: mx.array_equal(s["v"], mx.zeros_like(p)),
                    params,
                    optim.state,
                )
            )
            self.assertTrue(
                tree_equal(
                    lambda p, s: mx.array_equal(s["m"], mx.zeros_like(p)),
                    params,
                    optim.state,
                )
            )

    @unittest.skipIf(not has_torch, "requires Torch")
    def test_adamw_matches_pytorch(self):
        mx.random.seed(0)
        np.random.seed(0)

        model = nn.Linear(3, 1)
        init_weight = np.array(model.weight.tolist())
        init_bias = np.array(model.bias.tolist())

        def loss_fn(model, x, y):
            pred = model(x)
            return nn.losses.mse_loss(pred, y)

        x = np.random.rand(3, 3)
        y = np.random.rand(3, 1)

        optimizer = opt.AdamW(learning_rate=3e-4, bias_correction=True)
        loss_and_grad_fn = nn.value_and_grad(model, loss_fn)
        loss, grads = loss_and_grad_fn(model, mx.array(x), mx.array(y))
        optimizer.update(model, grads)

        # Equivalent torch code
        torch_model = torch.nn.Linear(3, 1)

        # copy over the parameters
        torch_model.weight.data = torch.tensor(init_weight, dtype=torch.float32)
        torch_model.bias.data = torch.tensor(init_bias, dtype=torch.float32)

        torch_optimizer = torch.optim.AdamW(torch_model.parameters(), lr=3e-4)
        torch_optimizer.zero_grad()
        pred = torch_model(torch.tensor(x, dtype=torch.float32))
        loss = torch.nn.MSELoss()(pred, torch.tensor(y, dtype=torch.float32))
        loss.backward()
        torch_optimizer.step()

        for name, param in torch_model.named_parameters():
            mlx_grad = np.array(grads[name])
            torch_grad = param.grad.detach().numpy()
            self.assertTrue(np.allclose(torch_grad, mlx_grad))

        for name, param in torch_model.named_parameters():
            mlx_param = np.array(model[name])
            torch_param = param.data.detach().numpy()
            self.assertTrue(np.allclose(torch_param, mlx_param))

    def test_lion(self):
        params = {
            "first": [mx.zeros((10,)), mx.zeros((1,))],
            "second": mx.zeros((1,)),
        }
        grads = tree_map(lambda x: mx.ones_like(x), params)

        # Explicit init
        optim = opt.Lion(learning_rate=1e-2)
        optim.init(params)
        self.assertTrue(
            tree_equal(
                lambda p, s: mx.array_equal(s["m"], mx.zeros_like(p)),
                params,
                optim.state,
            )
        )

    def test_adafactor(self):
        x = mx.zeros((5, 5))
        params = {"x": x}
        grad = {"x": mx.ones_like(x)}
        optimizer = opt.Adafactor()
        for _ in range(2):
            xp = optimizer.apply_gradients(grad, params)
            self.assertEqual(xp["x"].dtype, x.dtype)
            self.assertEqual(xp["x"].shape, x.shape)

        x = mx.zeros((5, 5), mx.float16)
        params = {"x": x}
        grad = {"x": mx.ones_like(x)}
        optimizer = opt.Adafactor()
        for _ in range(2):
            xp = optimizer.apply_gradients(grad, params)
            self.assertEqual(xp["x"].dtype, x.dtype)
            self.assertEqual(xp["x"].shape, x.shape)
        self.assertEqual(optimizer.state["step"], 2)

    def test_compiled_optimizer(self):
        model = nn.Linear(10, 10)
        x = mx.random.uniform(shape=(2, 10))
        optim = opt.SGD(learning_rate=1e-2, momentum=0.9)

        orig_params = model.parameters()

        def loss(model, x):
            return model(x).sum()

        # Uncompiled version
        def step(x):
            _, grad = nn.value_and_grad(model, loss)(model, x)
            optim.update(model, grad)

        step(x)
        uncompiled_params = model.parameters()

        # Pure version
        def loss(params, x):
            model.update(params)
            return model(x).sum()

        model.update(orig_params)
        optim = opt.SGD(learning_rate=1e-2, momentum=0.9)

        @mx.compile
        def step(params, opt_state, x):
            grad = mx.grad(loss)(params, x)
            optim.state = opt_state
            params = optim.apply_gradients(grad, params)
            return params, optim.state

        optim.init(model.parameters())
        pure_params, _ = step(model.parameters(), optim.state, x)
        self.assertTrue(mx.allclose(pure_params["weight"], uncompiled_params["weight"]))
        self.assertTrue(mx.allclose(pure_params["bias"], uncompiled_params["bias"]))

        # Impure version
        def loss(model, x):
            return model(x).sum()

        model.update(orig_params)
        optim = opt.SGD(learning_rate=1e-2, momentum=0.9)
        state = [model.state, optim.state]

        @partial(mx.compile, inputs=state, outputs=state)
        def step(x):
            _, grad = nn.value_and_grad(model, loss)(model, x)
            optim.update(model, grad)

        step(x)
        impure_params = model.parameters()
        self.assertTrue(
            mx.allclose(impure_params["weight"], uncompiled_params["weight"])
        )
        self.assertTrue(mx.allclose(impure_params["bias"], uncompiled_params["bias"]))

    def test_update_lr_compiled(self):
        params = {"w": mx.ones((5, 5))}
        grads = tree_map(lambda x: mx.ones_like(x), params)
        optim = opt.SGD(-1.0)

        @partial(mx.compile, inputs=optim.state)
        def update(grads):
            return optim.apply_gradients(grads, params)

        result = update(grads)
        self.assertTrue(mx.allclose(result["w"], mx.full((5, 5), 2.0)))
        optim.learning_rate = -2.0
        result = update(grads)
        self.assertTrue(mx.allclose(result["w"], mx.full((5, 5), 3.0)))


class TestSchedulers(mlx_tests.MLXTestCase):
    def test_decay_lr(self):
        for optim_class in optimizers_dict.values():
            lr_schedule = opt.step_decay(1e-1, 0.9, 1)
            optimizer = optim_class(learning_rate=lr_schedule)

            params = {"w": mx.ones((5, 5))}
            grads = tree_map(lambda x: mx.ones_like(x), params)

            for it in range(10):
                optimizer.apply_gradients(grads, params)
                expected_lr = 0.1 * (0.9**it)
                self.assertAlmostEqual(optimizer.learning_rate, expected_lr, delta=1e-7)

    def test_step_decay(self):
        lr_schedule = opt.step_decay(1e-1, 0.9, 1000)
        lr = lr_schedule(2500)
        expected_lr = 0.1 * (0.9**2)
        self.assertAlmostEqual(lr, expected_lr, delta=1e-7)

    def test_exponential_decay(self):
        lr_schedule = opt.exponential_decay(1e-1, 0.99)
        lr = lr_schedule(10)
        expected_lr = 0.1 * (0.99**10)
        self.assertAlmostEqual(lr, expected_lr, delta=1e-7)

    def test_cosine_decay(self):
        lr_schedule = opt.cosine_decay(0.1, 10)
        lr = lr_schedule(4)
        expected_lr = 0.1 * 0.5 * (1.0 + math.cos(math.pi * 4 / 10))
        self.assertAlmostEqual(lr, expected_lr, delta=1e-7)

        lr_schedule = opt.cosine_decay(0.1, 10, 0.05)
        lr = lr_schedule(9)
        expected_end_lr = 0.05
        self.assertGreater(lr, expected_end_lr)
        lr = lr_schedule(20)
        self.assertEqual(lr, expected_end_lr)

    def test_schedule_joiner(self):
        boundaries = [2, 3, 4]
        schedules = [lambda _: 3, lambda _: 4, lambda _: 5]
        with self.assertRaises(ValueError):
            opt.schedulers.join_schedules(schedules, boundaries)
        boundaries = [2, 4]
        schedule = opt.schedulers.join_schedules(schedules, boundaries)
        self.assertEqual(schedule(0).item(), 3)
        self.assertEqual(schedule(1).item(), 3)
        self.assertEqual(schedule(2).item(), 4)
        self.assertEqual(schedule(3).item(), 4)
        self.assertEqual(schedule(5).item(), 5)
        self.assertEqual(schedule(7).item(), 5)

    def test_linear_warmup_with_cosine_decay(self):
        warmup_schedule = opt.schedulers.linear_schedule(0.0, 1e-5, 100)
        cosine_schedule = opt.schedulers.cosine_decay(1e-5, 100)
        cos_with_warmup = opt.schedulers.join_schedules(
            [warmup_schedule, cosine_schedule], [101]
        )
        self.assertEqual(cos_with_warmup(0), 0.0)
        self.assertAlmostEqual(cos_with_warmup(101), 1e-5, delta=1e-1)
        optimizer = opt.Adam(learning_rate=cos_with_warmup)
        for _ in range(100):
            optimizer.update({}, {})
        self.assertAlmostEqual(optimizer.learning_rate.item(), 1e-5, delta=1e-1)
        for _ in range(100):
            optimizer.update({}, {})
        expected_lr = 1e-5 * 0.5 * (1.0 + math.cos(math.pi * 200 / 10))
        self.assertAlmostEqual(optimizer.learning_rate.item(), expected_lr, delta=1e-1)

    def test_compile_with_schedule(self):
        lr_schedule = opt.exponential_decay(1e-1, 0.9)
        optimizer = opt.SGD(learning_rate=lr_schedule)

        @partial(mx.compile, inputs=optimizer.state, outputs=optimizer.state)
        def update():
            optimizer.update({}, {})

        for step in range(5):
            update()
            self.assertAlmostEqual(lr_schedule(step), optimizer.learning_rate.item())

    def test_clip_grad_norm(self):
        # Test with small gradients that do not require clipping
        small_grads = {
            "first": [mx.array([0.1, 0.2]), mx.array([0.1])],
            "second": mx.array([0.3]),
        }
        max_norm = 10.0  # A large max_norm that shouldn't trigger clipping
        clipped_grads, total_norm = opt.clip_grad_norm(small_grads, max_norm)
        self.assertTrue(
            tree_equal(lambda x, y: mx.array_equal(x, y), small_grads, clipped_grads),
            "Gradients should not be modified when clipping is not necessary.",
        )

        # Test with large gradients that require clipping
        large_grads = {
            "first": [mx.array([10, 20]), mx.array([10])],
            "second": mx.array([30]),
        }
        max_norm = 1.0  # A small max_norm that should trigger clipping
        clipped_grads, total_norm = opt.clip_grad_norm(large_grads, max_norm)
        # Correctly extract only the gradient values for norm calculation
        clipped_values = [value for _, value in tree_flatten(clipped_grads)]
        norm_of_clipped = mx.sqrt(
            sum(mx.square(g).sum() for g in clipped_values)
        ).item()
        self.assertAlmostEqual(
            norm_of_clipped,
            max_norm,
            places=6,
            msg="Clipped gradients norm should be close to the specified max_norm.",
        )

        # Ensures that the scaling was done correctly
        scale = max_norm / total_norm
        expected_grads = tree_map(lambda g: g * scale, large_grads)
        self.assertTrue(
            tree_equal(
                lambda x, y: mx.allclose(x, y, atol=1e-6), expected_grads, clipped_grads
            ),
            "Gradients were not scaled correctly during clipping.",
        )

    def test_init_from_state(self):
        class Model(nn.Module):
            def __init__(self):
                super().__init__()
                self.l1 = nn.Linear(2, 2)
                self.drop = nn.Dropout(p=0.5)
                self.l2 = nn.Linear(2, 2)
                self.vals = [nn.Linear(2, 2), nn.ReLU(), nn.ReLU()]

        model = Model()
        optimizer = opt.Adam(learning_rate=3e-4)
        optimizer.init(model.trainable_parameters())

        # Flatten the state for serialization
        state = tree_flatten(optimizer.state)

        # Make a new optimizer and load the state
        optimizer = opt.Adam(learning_rate=3e-4)
        optimizer.state = tree_unflatten(state)

        # This should work without any errors
        grads = model.trainable_parameters()
        optimizer.update(model, grads)

    def test_multi_optimizer(self):
        class Model(nn.Module):
            def __init__(self):
                super().__init__()
                self.l1 = nn.Linear(2, 2)
                self.drop = nn.Dropout(p=0.5)
                self.l2 = nn.Linear(2, 2)
                self.vals = [nn.Linear(2, 2), nn.ReLU(), nn.ReLU()]

        model = Model()
        optimizer = opt.MultiOptimizer(
            [opt.Adam(learning_rate=0.001), opt.SGD(learning_rate=0.1)],
            [lambda name, weight: weight.ndim > 1],
        )
        optimizer.init(model.trainable_parameters())

        self.assertEqual(len(optimizer.state["states"]), 2)

        adam_states = tree_flatten(optimizer.state["states"][0])
        sgd_states = tree_flatten(optimizer.state["states"][1])
        self.assertEqual((len(sgd_states) - 2) * 2, len(adam_states) - 2)
        self.assertFalse(any("bias" in k for k, v in adam_states))
        self.assertFalse(any("weight" in k for k, v in sgd_states))


if __name__ == "__main__":
    mlx_tests.MLXTestRunner()
