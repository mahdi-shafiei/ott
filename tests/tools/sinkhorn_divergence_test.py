# Copyright OTT-JAX
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import functools
from typing import Any, Dict, Optional, Tuple

import pytest

import jax
import jax.numpy as jnp
import numpy as np

from ott.geometry import costs, geometry, grid, pointcloud
from ott.solvers import linear
from ott.solvers.linear import acceleration
from ott.tools import sinkhorn_divergence
from ott.tools.gaussian_mixture import gaussian_mixture


class TestSinkhornDivergence:

  @pytest.fixture(autouse=True)
  def setUp(self, rng: jax.Array):
    self._dim = 4
    self._num_points = 13, 17
    self.rng, *rngs = jax.random.split(rng, 3)
    a = jax.random.uniform(rngs[0], (self._num_points[0],))
    b = jax.random.uniform(rngs[1], (self._num_points[1],))
    self._a = a / jnp.sum(a)
    self._b = b / jnp.sum(b)

  @pytest.mark.fast(
      "cost_fn,rank", [(costs.SqEuclidean(), -1), (costs.Euclidean(), -1),
                       (costs.SqPNorm(2.1), -1), (costs.SqEuclidean(), 3)],
      only_fast=0
  )
  def test_euclidean_point_cloud(self, cost_fn: costs.CostFn, rank: int):

    is_low_rank = rank > 0
    rngs = jax.random.split(self.rng, 2)
    x = jax.random.uniform(rngs[0], (self._num_points[0], self._dim))
    y = jax.random.uniform(rngs[1], (self._num_points[1], self._dim))

    epsilon = 5e-2
    sd = functools.partial(
        sinkhorn_divergence.sinkdiv,
        solve_kwargs={
            "rank": rank,
            "inner_iterations": 1
        }
    )
    div, out = jax.jit(sd)(
        x, y, cost_fn=cost_fn, epsilon=epsilon, a=self._a, b=self._b
    )

    assert div >= 0.0
    assert out.is_low_rank == is_low_rank
    if is_low_rank:
      assert out.potentials is None
      np.testing.assert_equal(len(out.factors), 3)
    else:
      assert out.factors is None
      np.testing.assert_equal(len(out.potentials), 3)

    # Less iterations for (x,y) comparison, vs. (x,x) and (y,y)
    iters_xy, iters_xx, iters_yy = out.n_iters
    assert iters_xx < iters_xy
    assert iters_yy < iters_xy

    # Check computation of divergence matches that done separately.
    geometry_xy = pointcloud.PointCloud(x, y, epsilon=epsilon, cost_fn=cost_fn)
    geometry_xx = pointcloud.PointCloud(x, epsilon=epsilon, cost_fn=cost_fn)
    geometry_yy = pointcloud.PointCloud(y, epsilon=epsilon, cost_fn=cost_fn)

    div2_xy = linear.solve(
        geometry_xy, a=self._a, b=self._b, rank=rank
    ).reg_ot_cost
    div2_xx = linear.solve(
        geometry_xx, a=self._a, b=self._a, rank=rank
    ).reg_ot_cost
    div2_yy = linear.solve(
        geometry_yy, a=self._b, b=self._b, rank=rank
    ).reg_ot_cost

    # Check passing offset when using static_b works
    div_offset, _ = sd(
        x,
        y,
        cost_fn=cost_fn,
        epsilon=epsilon,
        a=self._a,
        b=self._b,
        static_b=True,
        offset_static_b=div2_yy
    )

    if is_low_rank:
      np.testing.assert_allclose(
          div, div2_xy - 0.5 * (div2_xx + div2_yy), rtol=1e-3, atol=1e-3
      )
      np.testing.assert_allclose(div, div_offset, rtol=1e-4, atol=1e-4)
    else:
      np.testing.assert_allclose(
          div, div2_xy - 0.5 * (div2_xx + div2_yy), rtol=1e-5, atol=1e-5
      )
      np.testing.assert_allclose(div, div_offset, rtol=1e-5, atol=1e-5)

    # Check gradient is finite
    grad = jax.jit(jax.grad(sd, has_aux=True, argnums=0))
    np.testing.assert_array_equal(
        jnp.isfinite(grad(x, y, cost_fn=cost_fn, epsilon=epsilon)[0]), True
    )

    # Test divergence of x to itself close to 0.
    div, out = sinkhorn_divergence.sinkhorn_divergence(
        pointcloud.PointCloud,
        x,
        x,
        cost_fn=cost_fn,
        epsilon=1e-1,
        solve_kwargs={
            "inner_iterations": 1,
            "threshold": 1e-5,
            "rank": rank,
        },
    )
    np.testing.assert_allclose(div, 0.0, rtol=1e-5, atol=1e-5)
    iters_xx, iters_xx_sym, _ = out.n_iters

    if is_low_rank:
      # no symmetric updates
      assert iters_xx_sym == iters_xx
    else:
      assert iters_xx_sym < iters_xx

  @pytest.mark.fast()
  def test_euclidean_autoepsilon(self):
    rngs = jax.random.split(self.rng, 2)
    cloud_a = jax.random.uniform(rngs[0], (self._num_points[0], self._dim))
    cloud_b = jax.random.uniform(rngs[1], (self._num_points[1], self._dim))
    div, out = sinkhorn_divergence.sinkhorn_divergence(
        pointcloud.PointCloud,
        cloud_a,
        cloud_b,
        a=self._a,
        b=self._b,
        solve_kwargs={"threshold": 1e-2},
    )
    assert div > 0.0
    assert len(out.potentials) == 3
    assert len(out.geoms) == 3

    geom0, geom1, *_ = out.geoms

    assert geom0.epsilon_scheduler is not geom1.epsilon_scheduler
    assert geom0.epsilon == geom1.epsilon

  def test_euclidean_autoepsilon_not_share_epsilon(self):
    rngs = jax.random.split(self.rng, 2)
    cloud_a = jax.random.uniform(rngs[0], (self._num_points[0], self._dim))
    cloud_b = jax.random.uniform(rngs[1], (self._num_points[1], self._dim))
    div, out = sinkhorn_divergence.sinkhorn_divergence(
        pointcloud.PointCloud,
        cloud_a,
        cloud_b,
        a=self._a,
        b=self._b,
        solve_kwargs={"threshold": 1e-2},
        share_epsilon=False
    )
    assert jnp.abs(out.geoms[0].epsilon - out.geoms[1].epsilon) > 0

  def test_euclidean_point_cloud_weights_unb(self):
    rngs = jax.random.split(self.rng, 2)
    cloud_a = jax.random.uniform(rngs[0], (self._num_points[0], self._dim))
    cloud_b = jax.random.uniform(rngs[1], (self._num_points[1], self._dim))
    kwargs = {"a": self._a, "b": self._b}
    div, out = sinkhorn_divergence.sinkhorn_divergence(
        pointcloud.PointCloud,
        cloud_a,
        cloud_b,
        epsilon=0.1,
        solve_kwargs={
            "threshold": 1e-2,
            "tau_a": 0.8,
            "tau_b": 0.9
        },
        **kwargs
    )
    assert div > 0.0
    assert len(out.potentials) == 3
    assert len(out.geoms) == 3

  def test_generic_point_cloud_wrapper(self):
    rngs = jax.random.split(self.rng, 2)
    x = jax.random.uniform(rngs[0], (self._num_points[0], self._dim))
    y = jax.random.uniform(rngs[1], (self._num_points[1], self._dim))

    # Tests with 3 cost matrices passed as args
    cxy = jnp.sum(jnp.abs(x[:, jnp.newaxis] - y[jnp.newaxis, :]) ** 2, axis=2)
    cxx = jnp.sum(jnp.abs(x[:, jnp.newaxis] - x[jnp.newaxis, :]) ** 2, axis=2)
    cyy = jnp.sum(jnp.abs(y[:, jnp.newaxis] - y[jnp.newaxis, :]) ** 2, axis=2)
    div, out = sinkhorn_divergence.sinkhorn_divergence(
        geometry.Geometry,
        cxy,
        cxx,
        cyy,
        epsilon=0.1,
        a=self._a,
        b=self._b,
        solve_kwargs={"threshold": 1e-2},
    )
    assert div > 0.0
    assert len(out.potentials) == 3
    assert len(out.geoms) == 3

    # Tests with 2 cost matrices passed as args
    div, out = sinkhorn_divergence.sinkhorn_divergence(
        geometry.Geometry,
        cxy,
        cxx,
        epsilon=0.1,
        a=self._a,
        b=self._b,
        solve_kwargs={"threshold": 1e-2},
    )
    assert div > 0.0
    assert len(out.potentials) == 3
    assert len(out.geoms) == 3

    # Tests with 3 cost matrices passed as kwargs
    div, out = sinkhorn_divergence.sinkhorn_divergence(
        geometry.Geometry,
        cost_matrix=(cxy, cxx, cyy),
        epsilon=0.1,
        a=self._a,
        b=self._b,
        solve_kwargs={"threshold": 1e-2},
    )
    assert div > 0.0
    assert len(out.potentials) == 3
    assert len(out.geoms) == 3

  @pytest.mark.parametrize("shuffle", [False, True])
  def test_segment_sinkdiv_result(self, shuffle: bool):
    # Test that segmented sinkhorn gives the same results:
    rngs = jax.random.split(self.rng, 4)
    x = jax.random.uniform(rngs[0], (self._num_points[0], self._dim))
    y = jax.random.uniform(rngs[1], (self._num_points[1], self._dim))
    geom_kwargs = {"epsilon": 0.01}
    solve_kwargs = {"threshold": 1e-2}
    true_divergence, _ = sinkhorn_divergence.sinkhorn_divergence(
        pointcloud.PointCloud,
        x,
        y,
        a=self._a,
        b=self._b,
        solve_kwargs=solve_kwargs,
        **geom_kwargs
    )

    if shuffle:
      # Now, shuffle the order of both arrays, but
      # still maintain the segment assignments:
      idx_x = jax.random.permutation(
          rngs[2], jnp.arange(x.shape[0] * 2), independent=True
      )
      idx_y = jax.random.permutation(
          rngs[3], jnp.arange(y.shape[0] * 2), independent=True
      )
    else:
      idx_x = jnp.arange(x.shape[0] * 2)
      idx_y = jnp.arange(y.shape[0] * 2)

    # Duplicate arrays:
    x_copied = jnp.concatenate((x, x))[idx_x]
    a_copied = jnp.concatenate((self._a, self._a))[idx_x]
    segment_ids_x = jnp.arange(2).repeat(x.shape[0])[idx_x]

    y_copied = jnp.concatenate((y, y))[idx_y]
    b_copied = jnp.concatenate((self._b, self._b))[idx_y]
    segment_ids_y = jnp.arange(2).repeat(y.shape[0])[idx_y]

    segmented_divergences = sinkhorn_divergence.segment_sinkhorn_divergence(
        x_copied,
        y_copied,
        num_segments=2,
        max_measure_size=19,
        segment_ids_x=segment_ids_x,
        segment_ids_y=segment_ids_y,
        indices_are_sorted=False,
        weights_x=a_copied,
        weights_y=b_copied,
        solve_kwargs=solve_kwargs,
        **geom_kwargs
    )

    np.testing.assert_allclose(
        true_divergence.repeat(2), segmented_divergences, rtol=1e-6, atol=1e-6
    )

  def test_segment_sinkdiv_different_segment_sizes(self):
    # Test other array sizes
    x1 = jnp.arange(10)[:, None].repeat(2, axis=1) - 0.1
    y1 = jnp.arange(11)[:, None].repeat(2, axis=1) + 0.1

    # Should have larger divergence since further apart:
    x2 = jnp.arange(12)[:, None].repeat(2, axis=1) - 0.1
    y2 = 2 * jnp.arange(13)[:, None].repeat(2, axis=1) + 0.1

    sink_div = jax.jit(
        sinkhorn_divergence.segment_sinkhorn_divergence,
        static_argnames=["num_per_segment_x", "num_per_segment_y"],
    )

    segmented_divergences = sink_div(
        jnp.concatenate((x1, x2)),
        jnp.concatenate((y1, y2)),
        # these 2 arguments are not necessary for jitting:
        # num_segments=2,
        # max_measure_size=15,
        num_per_segment_x=(10, 12),
        num_per_segment_y=(11, 13),
        epsilon=0.01
    )

    assert segmented_divergences.shape[0] == 2
    assert segmented_divergences[1] > segmented_divergences[0]

    true_divergences = jnp.array([
        sinkhorn_divergence.sinkhorn_divergence(
            pointcloud.PointCloud, x, y, epsilon=0.01
        )[0] for x, y in zip((x1, x2), (y1, y2))
    ])
    np.testing.assert_allclose(
        segmented_divergences, true_divergences, rtol=1e-6, atol=1e-6
    )

  def test_segment_sinkdiv_custom_padding(self, rng: jax.Array):
    rngs = jax.random.split(rng, 4)
    dim = 3
    b_cost = costs.Bures(dim)
    epsilon = 0.2
    num_segments = 2

    num_per_segment_x = (5, 2)
    num_per_segment_y = (3, 5)
    ns = num_per_segment_x + num_per_segment_y

    means_and_covs_to_x = jax.vmap(
        costs.mean_and_cov_to_x, in_axes=[0, 0, None]
    )

    def g(rng, n):
      out = gaussian_mixture.GaussianMixture.from_random(
          rng, n_components=n, n_dimensions=dim
      )
      return means_and_covs_to_x(out.loc, out.covariance, dim)

    x1, x2, y1, y2 = (g(rngs[i], ns[i]) for i in range(4))

    true_divergences = jnp.array([
        sinkhorn_divergence.sinkhorn_divergence(
            pointcloud.PointCloud, x, y, epsilon=epsilon, cost_fn=b_cost
        )[0] for x, y in zip((x1, x2), (y1, y2))
    ])

    x = jnp.vstack((x1, x2))
    y = jnp.vstack((y1, y2))

    segmented_divergences = sinkhorn_divergence.segment_sinkhorn_divergence(
        x,
        y,
        num_segments=num_segments,
        max_measure_size=5,
        num_per_segment_x=num_per_segment_x,
        num_per_segment_y=num_per_segment_y,
        epsilon=epsilon,
        cost_fn=b_cost
    )

    np.testing.assert_allclose(
        segmented_divergences, true_divergences, rtol=1e-6, atol=1e-6
    )

  # yapf: disable
  @pytest.mark.fast.with_args(
      "solve_kwargs,epsilon", [
          ({"anderson": acceleration.AndersonAcceleration(memory=3)}, 1e-2),
          ({"anderson": acceleration.AndersonAcceleration(memory=6)}, None),
          ({"momentum": acceleration.Momentum(start=30)}, None),
          ({"momentum": acceleration.Momentum(value=1.05)}, 1e-3),
      ],
      only_fast=[0, -1],
  )
  # yapf: enable
  def test_euclidean_momentum_params(
      self, solve_kwargs: Dict[str, Any], epsilon: Optional[float]
  ):
    # check if sinkhorn divergence solve_kwargs parameters used for
    # momentum/Anderson are properly overridden for the symmetric (x,x) and
    # (y,y) parts.
    rngs = jax.random.split(self.rng, 2)
    threshold = 3.2e-3
    cloud_a = jax.random.uniform(rngs[0], (self._num_points[0], self._dim))
    cloud_b = jax.random.uniform(rngs[1], (self._num_points[1], self._dim))
    solve_kwargs["threshold"] = threshold

    div, out = sinkhorn_divergence.sinkhorn_divergence(
        pointcloud.PointCloud,
        cloud_a,
        cloud_b,
        epsilon=epsilon,
        a=self._a,
        b=self._b,
        solve_kwargs=solve_kwargs,
    )
    assert div > 0.0
    assert threshold > out.errors[0][-1]
    assert threshold > out.errors[1][-1]
    assert threshold > out.errors[2][-1]


class TestSinkhornDivergenceGrad:

  @pytest.fixture(autouse=True)
  def initialize(self, rng: jax.Array):
    self._dim = 3
    self._num_points = 13, 12
    self.rng, *rngs = jax.random.split(rng, 3)
    a = jax.random.uniform(rngs[0], (self._num_points[0],))
    b = jax.random.uniform(rngs[1], (self._num_points[1],))
    self._a = a / jnp.sum(a)
    self._b = b / jnp.sum(b)

  def test_gradient_generic_point_cloud_wrapper(self):
    rngs = jax.random.split(self.rng, 3)
    x = jax.random.uniform(rngs[0], (self._num_points[0], self._dim))
    y = jax.random.uniform(rngs[1], (self._num_points[1], self._dim))

    loss_fn = lambda x, y: sinkhorn_divergence.sinkhorn_divergence(
        geom=pointcloud.PointCloud,
        x=x,
        y=y,
        epsilon=1.0,
        a=self._a,
        b=self._b,
        solve_kwargs={"threshold": 0.05}
    )

    delta = jax.random.normal(rngs[2], x.shape)
    eps = 1e-3  # perturbation magnitude

    # automatic differentiation gradient
    loss_and_grad = jax.jit(
        jax.value_and_grad(loss_fn, has_aux=True, argnums=0)
    )
    (loss_value, _), grad_loss = loss_and_grad(x, y)
    custom_grad = jnp.sum(delta * grad_loss)

    assert not jnp.isnan(loss_value)
    np.testing.assert_array_equal(grad_loss.shape, x.shape)
    np.testing.assert_array_equal(jnp.isnan(grad_loss), False)

    # second calculation of gradient
    loss_delta_plus, _ = loss_fn(x + eps * delta, y)
    loss_delta_minus, _ = loss_fn(x - eps * delta, y)
    finite_diff_grad = (loss_delta_plus - loss_delta_minus) / (2 * eps)

    np.testing.assert_allclose(
        custom_grad, finite_diff_grad, rtol=1e-2, atol=1e-2
    )

  @pytest.mark.parametrize("grid_size", [(5,), (2, 3), (3, 4, 5)])
  def test_grid_geometry(self, rng: jax.Array, grid_size: Tuple[int, ...]):
    rng1, rng2 = jax.random.split(rng, 2)
    gs = (5,)

    a = jax.random.uniform(rng1, shape=gs)
    a = a / jnp.sum(a)
    b = jax.random.uniform(rng2, shape=gs)
    b = b / jnp.sum(b)

    div, _ = sinkhorn_divergence.sinkhorn_divergence(
        grid.Grid,
        grid_size=gs,
        a=a,
        b=b,
        epsilon=1e-1,
    )

    assert jnp.isfinite(div)
