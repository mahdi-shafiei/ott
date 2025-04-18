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
from typing import List, Optional, Sequence, Tuple, Union

import jax
import jax.numpy as jnp
import numpy as np
import scipy.sparse as sp

from ott.experimental import mmsinkhorn
from ott.geometry import pointcloud
from ott.solvers.linear import sinkhorn, sinkhorn_lr
from ott.solvers.quadratic import gromov_wasserstein

try:
  import matplotlib.colors as mcolors
  import matplotlib.patches as ptc
  import matplotlib.pyplot as plt
  from matplotlib import animation
except ImportError:
  plt = animation = None

# TODO(michalk8): make sure all outputs conform to a unified transport interface
Transport = Union[sinkhorn.SinkhornOutput, sinkhorn_lr.LRSinkhornOutput,
                  gromov_wasserstein.GWOutput]


@jax.jit
def ccworder(A: jnp.ndarray) -> jnp.ndarray:
  """Helper function to plot good-looking polygons.

  https://stackoverflow.com/questions/5040412/how-to-draw-the-largest-polygon-from-a-set-of-points
  """
  A = A - jnp.mean(A, 0, keepdims=True)
  return jnp.argsort(jnp.arctan2(A[:, 1], A[:, 0]))


def bidimensional(x: jnp.ndarray,
                  y: jnp.ndarray) -> Tuple[jnp.ndarray, jnp.ndarray]:
  """Apply PCA to reduce to bi-dimensional data."""
  if x.shape[1] < 3:
    return x, y

  u, s, _ = sp.linalg.svds(np.array(jnp.concatenate([x, y], axis=0)), k=2)
  proj = u * s
  k = x.shape[0]
  return proj[:k], proj[k:]


class Plot:
  """Plot an optimal transport map between two point clouds.

  This object can either plot or update a plot, to create animations as a
  :class:`~matplotlib.animation.FuncAnimation`, which can in turned be saved to
  disk at will. There are two design principles here:

  #. we do not rely on saving to/loading from disk to create animations
  #. we try as much as possible to disentangle the transport problem from
     its visualization.

  We use 2D scatter plots by default, relying on PCA visualization for d>3 data.
  This step requires a conversion to a numpy array, in order to compute leading
  singular values. This tool is therefore not designed having performance in
  mind.

  Args:
    fig: Specify figure object. Created by default
    ax: Specify axes objects. Created by default
    threshold: value below which links in transportation matrix won't be
      plotted. This value should be negative when using animations.
    scale: scale used for marker plots.
    show_lines: whether to show OT lines, as described in ``ot.matrix`` argument
    cmap: color map used to plot line colors.
    scale_alpha_by_coupling: use or not the coupling's value as proxy for alpha
    alpha: default alpha value for lines.
    title: title of the plot.
  """

  def __init__(
      self,
      fig: Optional["plt.Figure"] = None,
      ax: Optional["plt.Axes"] = None,
      threshold: float = -1.0,
      scale: int = 200,
      show_lines: bool = True,
      cmap: str = "cool",
      scale_alpha_by_coupling: bool = False,
      alpha: float = 0.7,
      title: Optional[str] = None,
      xlim: Optional[List[float]] = None,
      ylim: Optional[List[float]] = None,
  ):
    if plt is None:
      raise RuntimeError("Please install `matplotlib` first.")

    if ax is None and fig is None:
      fig, ax = plt.subplots()
    elif fig is None:
      fig = plt.gcf()
    elif ax is None:
      ax = plt.gca()
    self.fig = fig
    self.ax = ax
    self._show_lines = show_lines
    self._lines = []
    self._points_x = None
    self._points_y = None
    self._threshold = threshold
    self._scale = scale
    self._cmap = cmap
    self._scale_alpha_by_coupling = scale_alpha_by_coupling
    self._alpha = alpha
    self._title = title
    self._xlim = xlim
    self._ylim = ylim

  def _scatter(self, ot: Transport):
    """Compute the position and scales of the points on a 2D plot."""
    if not isinstance(ot.geom, pointcloud.PointCloud):
      raise ValueError("So far we only plot PointCloud geometry.")

    x, y = ot.geom.x, ot.geom.y
    a, b = ot.a, ot.b
    x, y = bidimensional(x, y)
    scales_x = a * self._scale * a.shape[0]
    scales_y = b * self._scale * b.shape[0]
    return x, y, scales_x, scales_y

  def _mapping(self, x: jnp.ndarray, y: jnp.ndarray, matrix: jnp.ndarray):
    """Compute the lines representing the mapping between the 2 point clouds."""
    # Only plot the lines with a cost above the threshold.
    u, v = jnp.where(matrix > self._threshold)
    c = matrix[jnp.where(matrix > self._threshold)]
    xy = jnp.concatenate([x[u], y[v]], axis=-1)

    # Check if we want to adjust transparency.
    scale_alpha_by_coupling = self._scale_alpha_by_coupling

    # We can only adjust transparency if max(c) != min(c).
    if scale_alpha_by_coupling:
      min_matrix, max_matrix = jnp.min(c), jnp.max(c)
      scale_alpha_by_coupling = max_matrix != min_matrix

    result = []
    for i in range(xy.shape[0]):
      strength = jnp.max(jnp.array(matrix.shape)) * c[i]
      if scale_alpha_by_coupling:
        normalized_strength = (c[i] - min_matrix) / (max_matrix - min_matrix)
        alpha = self._alpha * float(normalized_strength)
      else:
        alpha = self._alpha

      # Matplotlib's transparency is sensitive to numerical errors.
      alpha = np.clip(alpha, 0.0, 1.0)

      start, end = xy[i, [0, 2]], xy[i, [1, 3]]
      result.append((start, end, strength, alpha))

    return result

  def __call__(self, ot: Transport) -> List["plt.Artist"]:
    """Plot couplings in 2-D, using PCA if data is higher dimensional."""
    x, y, sx, sy = self._scatter(ot)
    self._points_x = self.ax.scatter(
        *x.T, s=sx, edgecolors="k", marker="o", label="x"
    )
    self._points_y = self.ax.scatter(
        *y.T, s=sy, edgecolors="k", marker="X", label="y"
    )
    self.ax.legend(fontsize=15)

    if self._title is not None:
      self.ax.set_title(self._title)

    if not self._show_lines:
      return []

    lines = self._mapping(x, y, ot.matrix)
    cmap = plt.get_cmap(self._cmap)
    self._lines = []
    for start, end, strength, alpha in lines:
      line, = self.ax.plot(
          start,
          end,
          linewidth=0.5 + 4 * strength,
          color=cmap(strength),
          zorder=0,
          alpha=alpha
      )
      self._lines.append(line)

    if self._xlim is not None:
      self.ax.set_xlim(self._xlim)
    if self._ylim is not None:
      self.ax.set_ylim(self._ylim)

    return [self._points_x, self._points_y] + self._lines

  def update(self,
             ot: Transport,
             title: Optional[str] = None) -> List["plt.Artist"]:
    """Update a plot with a transport instance."""
    x, y, _, _ = self._scatter(ot)
    self._points_x.set_offsets(x)
    self._points_y.set_offsets(y)

    if title is not None:
      self.ax.set_title(title)

    if not self._show_lines:
      return []

    new_lines = self._mapping(x, y, ot.matrix)
    cmap = plt.get_cmap(self._cmap)
    for line, new_line in zip(self._lines, new_lines):
      start, end, strength, alpha = new_line

      line.set_data(start, end)
      line.set_linewidth(0.5 + 4 * strength)
      line.set_color(cmap(strength))
      line.set_alpha(alpha)

    # Maybe add new lines to the plot.
    num_lines = len(self._lines)
    num_to_plot = len(new_lines) if self._show_lines else 0
    for i in range(num_lines, num_to_plot):
      start, end, strength, alpha = new_lines[i]

      line, = self.ax.plot(
          start,
          end,
          linewidth=0.5 + 4 * strength,
          color=cmap(strength),
          zorder=0,
          alpha=alpha
      )
      self._lines.append(line)

    self._lines = self._lines[:num_to_plot]  # Maybe remove some
    return [self._points_x, self._points_y] + self._lines

  def animate(
      self,
      transports: Sequence[Transport],
      titles: Optional[Sequence[str]] = None,
      frame_rate: float = 10.0
  ) -> "animation.FuncAnimation":
    """Make an animation from several transports."""
    _ = self(transports[0])
    if titles is None:
      titles = [None for _ in np.arange(0, len(transports))]
    assert len(titles) == len(transports), (
        f"titles/transports lengths differ `{len(titles)}`/`{len(transports)}`."
    )
    return animation.FuncAnimation(
        self.fig,
        lambda i: self.update(transports[i], titles[i]),
        np.arange(0, len(transports)),
        init_func=lambda: self.update(transports[0], titles[0]),
        interval=1000 / frame_rate,
        blit=True
    )


# TODO(zoepiran): add support for data of d > 2 (PCA on all k's)
class PlotMM(Plot):
  """Plots an optimal transport map for :class:`~ott.experimental.mmsinkhorn.MMSinkhorn`.

  It enables to either plot or update a plot in a single object, offering the
  possibilities to create animations as a
  :class:`~matplotlib.animation.FuncAnimation`, which can in turned be saved to
  disk at will. There are two design principles here:

  #. we do not rely on saving to/loading from disk to create animations
  #. we try as much as possible to disentangle the transport problem from
       its visualization.

  Args:
    fig: Specify figure object. Created by default
    ax: Specify axes objects. Created by default
    fix_axes_lim: Whether to fix x/y limits to :math:`[0, 1]`.
    cmap: color map used to plot line colors.
    markers: Markers for each marginal.
    alpha: default alpha value for lines.
    title: title of the plot.
  """  # noqa: E501

  def __init__(
      self,
      fig: Optional["plt.Figure"] = None,
      ax: Optional["plt.Axes"] = None,
      fix_axes_lim: bool = False,
      cmap: Union[str, "mcolors.Colormap"] = "cividis_r",
      markers: str = "svopxdh",
      alpha: float = 0.6,
      title: Optional[str] = None,
  ):
    if plt is None:
      raise RuntimeError("Please install `matplotlib` first.")
    if isinstance(cmap, str):
      cmap = plt.colormaps[cmap]
    super().__init__(fig=fig, ax=ax, cmap=cmap, alpha=alpha, title=title)
    self._patches = None
    self._points = None
    self._markers = markers
    self._fix_axes_lim = fix_axes_lim

  def __call__(
      self,
      ot: mmsinkhorn.MMSinkhornOutput,
      top_k: Optional[int] = None
  ) -> List["plt.Artist"]:
    """Plot 2-D couplings. does not support higher dimensional."""
    assert ot.n_marginals <= len(self._markers), "Not enough markers to plot."
    self._points = []
    self._patches = []
    n0 = max(ot.shape)
    top_k = n0 if top_k is None else top_k

    # extract the `top_k` entries in the tensor, and their indices.
    _, idx = jax.lax.top_k(ot.tensor.ravel(), top_k)
    indices = jnp.unravel_index(idx, ot.shape)

    alphas = np.linspace(self._alpha, 0.2, max(0, top_k - n0))
    for j in range(top_k):
      points = [x[indices[i][j], ...] for i, x in enumerate(ot.x_s)]
      # re-order to ensure polygons have maximal area
      points = [points[i] for i in ccworder(jnp.array(points))]
      alpha = self._alpha if j < n0 else alphas[j - n0]

      polygon = ptc.Polygon(
          points,
          fill=True,
          linewidth=2,
          color=self._cmap(float(j >= n0)),
          alpha=alpha,
          zorder=-j,
      )
      self._patches.append(self.ax.add_patch(polygon))

    for i, (x, a) in enumerate(zip(ot.x_s, ot.a_s)):
      points = self.ax.scatter(
          x[:, 0],
          x[:, 1],
          s=200.0 * len(a) * a,
          marker=self._markers[i],
          c="black",
          linewidth=0.0,
          edgecolor=None,
          label=str(i)
      )
      self._points.append(points)

    if self._title is not None:
      self.ax.set_title(self._title)

    return self._points + self._patches

  def update(
      self,
      ot: mmsinkhorn.MMSinkhornOutput,
      title: Optional[str] = None,
      top_k: Optional[int] = None,
  ) -> List["plt.Artist"]:
    """Update a plot with a transport instance."""
    n0 = max(ot.shape)
    top_k = n0 if top_k is None else top_k
    # extract the `top_k` entries in the tensor, and their indices.
    _, idx = jax.lax.top_k(ot.tensor.ravel(), top_k)
    indices = jnp.unravel_index(idx, ot.shape)

    alphas = np.linspace(self._alpha, 0.2, max(0, top_k - n0))
    for j, patch in enumerate(self._patches):
      points = [x[indices[i][j], ...] for i, x in enumerate(ot.x_s)]
      # re-order to ensure polygons have maximal area
      points = [points[i] for i in ccworder(jnp.array(points))]
      alpha = self._alpha if j < n0 else alphas[j - n0]
      # update the location of the patches according to the new coordinates
      patch.set_xy(points)
      patch.set_color(self._cmap(float(j >= n0)))
      patch.set_alpha(alpha)

    for points, xs in zip(self._points, ot.x_s):
      points.set_offsets(xs)

    if title is not None:
      self.ax.set_title(title)

    # we keep the axis fixed to 0-1 assuming normalized data
    if self._fix_axes_lim:
      eps = 2.5e-2
      self.ax.set_ylim(-eps, 1.0 + eps)
      self.ax.set_xlim(-eps, 1.0 + eps)

    return self._points + self._patches

  def animate(
      self,
      transports: Sequence[mmsinkhorn.MMSinkhornOutput],
      titles: Optional[Sequence[str]] = None,
      frame_rate: float = 10.0,
      top_k: Optional[int] = None,
  ) -> "animation.FuncAnimation":
    """Make an animation from several transports."""
    ot, *_ = transports
    _ = self(ot, top_k=top_k)
    titles = titles if titles is not None else [""] * len(transports)
    return animation.FuncAnimation(
        self.fig,
        lambda i: self.update(ot=transports[i], title=titles[i], top_k=top_k),
        np.arange(0, len(transports)),
        init_func=lambda: self.update(ot, title=titles[0], top_k=top_k),
        interval=1000.0 / frame_rate,
        blit=True,
    )
