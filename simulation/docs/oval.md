# Oval tracks

`tools/oval.py` builds an asymmetric oval with two variable-radius corners and two tangent straights. A study
uses it to make a closed track for the lap sim:

```python
from tools.oval import oval_centerline, oval_layout

center = oval_centerline(length_m=60.0, mean_radius_m=(15.0, 9.0), radius_rate_m_per_rad=(2.0, -1.5), spacing_m=0.5)
layout = oval_layout(length_m=60.0, mean_radius_m=(15.0, 9.0), radius_rate_m_per_rad=(2.0, -1.5))
```

- `oval_centerline` gives the centerline points in m, counter-clockwise. The loop starts at the mid heading of
  corner 0, so a lap starts in a corner, not at the start of a brake or drive event.
- `oval_layout` gives the turn angle, entry and exit radius and arc length of each corner, the straight lengths
  and the lap length.
- Both raise `ValueError` if the parameters give no valid oval (see [Validity](#validity)).
- `tools/test_oval.py` checks the template values below and the constant-radius case. `make test` runs it.

## Parameters

| Symbol | Meaning | Template value |
| ------ | ------- | -------------- |
| $L$ | distance between the corner centers $O_0$ and $O_1$ | 60 m |
| $R_0, R_1$ | mean radius of each corner, averaged over the turn angle | 15 m, 9 m |
| $R_0', R_1'$ | signed rate $dR/d\theta$ of each corner | 2 m/rad, −1.5 m/rad |

The car travels counter-clockwise. $\theta$ is the heading. $\hat t(\theta) = (\cos\theta, \sin\theta)$ is the
travel direction. $\hat n_R(\theta) = (\sin\theta, -\cos\theta)$ points to the right of travel.
$O_0 = (0, 0)$ and $O_1 = (L, 0)$.

## Corner $i$

The radius is linear in heading:

$$
R_i(\theta) = R_i + R_i'\,(\theta - \bar\theta_i)
$$

$\bar\theta_i$ is the mid heading of the corner. Thus $R_i$ is the mean of $R_i(\theta)$ over the corner.

The position is:

$$
\mathbf p_i(\theta) = O_i + R_i'\,\hat t(\theta) + R_i(\theta)\,\hat n_R(\theta)
$$

Its derivative is $d\mathbf p_i/d\theta = R_i(\theta)\,\hat t(\theta)$. So the curve has heading $\theta$ and
radius of curvature $R_i(\theta)$.

The center of curvature is $\mathbf c_i(\theta) = O_i + R_i'\,\hat t(\theta)$. It moves on a circle of radius
$|R_i'|$ about $O_i$. $O_i$ is the "corner center". When $R_i' = 0$, $O_i$ is the center of an ordinary arc.

The tangent line at heading $\theta$ is at distance $R_i(\theta)$ to the right of $O_i$:

$$
(\mathbf p_i(\theta) - O_i)\cdot\hat n_R(\theta) = R_i(\theta)
$$

The arc length is $s_i = R_i\,\Delta_i$, where $\Delta_i$ is the turn angle of corner $i$.

## Straights (C¹ joints)

The bottom straight has heading $\psi_b$. It goes from the exit of corner 0 to the entry of corner 1. The top
straight has heading $\psi_t$. It goes from the exit of corner 1 to the entry of corner 0.

| Corner | Heading range | Turn angle |
| ------ | ------------- | ---------- |
| 1 | $\psi_b \to \psi_t$ | $\Delta_1 = \psi_t - \psi_b$ |
| 0 | $\psi_t \to \psi_b + 2\pi$ | $\Delta_0 = 2\pi - \Delta_1$ |

A straight with heading $\psi$ is tangent to both corners if both corners give the same line. Both corners are on
the left of the straight. Use the tangent-line equation above:

$$
(O_1 - O_0)\cdot\hat n_R(\psi) = R_0(\psi) - R_1(\psi)
$$

Apply it at each straight. Use the entry and exit radii of each corner:

$$
\begin{aligned}
L\sin\psi_b &= \left(R_0 + \tfrac12 R_0'\Delta_0\right) - \left(R_1 - \tfrac12 R_1'\Delta_1\right) \\
L\sin\psi_t &= \left(R_0 - \tfrac12 R_0'\Delta_0\right) - \left(R_1 + \tfrac12 R_1'\Delta_1\right)
\end{aligned}
$$

Let $\mu = (\psi_b + \psi_t)/2$. Add and subtract the two equations:

$$
\begin{aligned}
L\sin\mu\,\cos\tfrac{\Delta_1}{2} &= R_0 - R_1 \\
L\cos\mu\,\sin\tfrac{\Delta_1}{2} &= -\tfrac12\left(R_0'\Delta_0 + R_1'\Delta_1\right)
\end{aligned}
$$

`tools/oval.py` solves these two equations for $\mu$ and $\Delta_1$ with `scipy.optimize.fsolve`, from
$(\pi/2, \pi)$. Then $\psi_{b,t} = \mu \mp \Delta_1/2$.

Each joint has the same point and the same heading on both sides. Thus the path is C¹. The curvature jumps at
each joint from $1/R_i(\psi)$ to 0. Thus the path is not C². A lap driver sees a step in path curvature at each
corner entry and exit.

Check: if $R_0' = R_1' = 0$, then $\mu = \pi/2$ and $\Delta_1 = 2\arccos\left((R_0 - R_1)/L\right)$. This is
the usual belt-around-two-pulleys result.

## Straight lengths

$$
\begin{aligned}
\ell_b &= L\cos\psi_b + R_1' - R_0' \\
\ell_t &= -L\cos\psi_t + R_0' - R_1'
\end{aligned}
$$

The lap length is $R_0\Delta_0 + R_1\Delta_1 + \ell_b + \ell_t$.

## Validity

The parameters give a valid oval only if all of these are true. `tools/oval.py` raises `ValueError` if one is
false.

- The two-equation solve converges with $0 < \Delta_1 < 2\pi$.
- Each radius stays positive: $R_i > \tfrac12 |R_i'|\,\Delta_i$.
- Each straight has positive length: $\ell_b > 0$ and $\ell_t > 0$.

## Template values

| Quantity | Corner 0 | Corner 1 |
| -------- | -------- | -------- |
| Turn angle | 191.5° | 168.5° |
| Entry radius | 11.7 m | 11.2 m |
| Exit radius | 18.3 m | 6.8 m |
| Arc length | 50.1 m | 26.5 m |

Straights: $\ell_b$ = 56.1 m, $\ell_t$ = 63.3 m.
