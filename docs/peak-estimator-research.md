# Sub-sample peak estimator research (notes)

Source: web research, 2026-07-30. Question: best extremum/symmetry-center
estimator for our bell-shaped frequency-vs-position scan curves (~250 samples across
the peak, 16 um spacing at 4 mm/s, sensor noise). Note: the survey below assumed
upstream uses a plain 3-point parabola; the actual upstream uses a Gaussian-weighted
quadratic least-squares fit over a window (see upstream-algorithms.md), which is
already close to method 2 below. The comparison remains valid.

## Methods surveyed

1. **3-point parabolic interpolation.** Classic spectral peak picking. Bias analysis:
   J.O. Smith, Spectral Audio Signal Processing (CCRMA), "Bias of Parabolic Peak
   Interpolation". Periodic shape-mismatch bias unless true peak is parabolic;
   uses only 3 samples so variance is high (discards the other ~247).

2. **Quadratic least-squares over a wider window.** Same shape-mismatch bias
   mechanism, but variance drops as noise/sqrt(N) (standard LS theory; O'Haver,
   terpconnect.umd.edu/~toh/spectrum/CurveFitting.html). Window must stay inside the
   region where the bell is well-approximated by a parabola (roughly +/-1..1.5 sigma
   for a Gaussian); wider windows bias the vertex via tail mismatch.

3. **Gaussian 3-point interpolation** (parabola on ln(y)). Standard PIV sub-pixel
   estimator: Willert & Gharib, Experiments in Fluids 10:181-193 (1991). Near-zero
   shape bias if the peak is Gaussian-like; comparative PIV studies (Lourenco &
   Krothapalli) rank Gaussian above parabolic. Generalizes to log-domain LS over a
   window (combining with method 2).

4. **Centroid / center of mass.** Documented peak-locking bias (MNRAS 476:300, 2018,
   Shack-Hartmann; CRLB analyses). Small at our oversampling, but sensitive to
   window-edge truncation and baseline drift. Not recommended as primary.

5. **Symmetry/mirror cross-correlation.** Find center maximizing agreement between
   curve and its mirror image; matched filtering under the symmetry assumption only.
   Uses all N samples, no local-shape assumption. Reference: "Two peak-finding
   algorithms for two-dimensional unimodal symmetric signals based on mirroring and
   interpolating," The Journal of Supercomputing (2025),
   link.springer.com/article/10.1007/s11227-025-07264-0. Lowest expected variance,
   weakest assumption (symmetry, which our physics already asserts).

## Researcher recommendation

Replace narrow local fits: for a densely sampled symmetric bell,
1st choice: symmetry-based mirror estimator (method 5);
2nd choice: Gaussian log-domain least-squares over a principled window (2+3).
Both are established, citable, and need no hand-tuned constants beyond a
data-justified window choice.

## Status

Not yet a decision. Estimator choice is a commitment point: consult advisor,
owner approves. Whatever is chosen, the estimator lives in one pure function so it
can be swapped and benchmarked against synthetic curves and real save_csv data.
