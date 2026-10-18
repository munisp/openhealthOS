"""MCMC estimation of per-facility fraud rates with partial pooling.

Why MCMC and not a closed form: a single facility's flagged-claim rate is
Beta-Binomial and conjugate, but the platform question is comparative —
"is THIS facility's rate unusual given the national rate?" — which needs a
hierarchical model. We sample the two-level model

    theta_i ~ Beta(mu * kappa, (1 - mu) * kappa)   (facilities pool toward mu)
    x_i     ~ Binomial(n_i, theta_i)               (flagged claims)

with a random-walk Metropolis-Hastings sampler in logit(mu) / log(kappa) /
logit(theta_i) space. Partial pooling shrinks tiny rural facilities (3
claims, 1 flagged = 33%?!) toward the national mean, while large facilities
with sustained flag rates stand out on their own evidence. That fixes the
classic false-positive pattern of naive rate ranking.

All stdlib: random.gauss / math only. Deterministic under a fixed seed so
audits can reproduce every number.
"""

import math
import random


def _logit(p):
    p = min(max(p, 1e-9), 1 - 1e-9)
    return math.log(p / (1 - p))


def _inv_logit(z):
    return 1.0 / (1.0 + math.exp(-z))


def _beta_logpdf(theta, a, b):
    theta = min(max(theta, 1e-12), 1 - 1e-12)
    return (a - 1) * math.log(theta) + (b - 1) * math.log(1 - theta)


def _binom_logpmf(x, n, p):
    p = min(max(p, 1e-12), 1 - 1e-12)
    return (math.lgamma(n + 1) - math.lgamma(x + 1) - math.lgamma(n - x + 1)
            + x * math.log(p) + (n - x) * math.log(1 - p))


def sample_posterior(observations, draws=4000, burn=1000, seed=42,
                     proposal_sd=(0.3, 0.3, 0.4), thin=1):
    """Metropolis-Hastings over (mu, kappa, theta_i).

    observations: {facility_id: {"claims": n, "flagged": x}}
    returns {"mu": [...], "kappa": [...], "theta": {fid: [...]}} post-burn.
    """
    rng = random.Random(seed)
    fids = list(observations)
    total_n = sum(o["claims"] for o in observations.values())
    total_x = sum(o["flagged"] for o in observations.values())
    if total_n == 0:
        raise ValueError("no claims observed")

    mu = _logit(max(total_x, 0.5) / total_n)
    kappa = math.log(20.0)
    theta = {f: _logit((o["flagged"] + 0.5) / (o["claims"] + 1.0))
             for f, o in observations.items()}

    sd_mu, sd_kappa, sd_theta = proposal_sd
    out = {"mu": [], "kappa": [], "theta": {f: [] for f in fids}}

    def log_post(mu_z, kappa_z, theta_z):
        m, k = _inv_logit(mu_z), math.exp(kappa_z)
        lp = -0.5 * (mu_z / 5.0) ** 2 - 0.5 * ((kappa_z - math.log(20.0)) / 3.0) ** 2
        a, b = m * k, (1 - m) * k
        for f in fids:
            t = _inv_logit(theta_z[f])
            lp += _beta_logpdf(t, a, b)
            o = observations[f]
            lp += _binom_logpmf(o["flagged"], o["claims"], t)
        return lp

    lp_cur = log_post(mu, kappa, theta)
    accepted = 0
    for it in range(draws + burn):
        # propose on one block at a time (component-wise MH)
        block = it % 3
        if block == 0:
            prop = mu + rng.gauss(0, sd_mu)
            lp_new = log_post(prop, kappa, theta)
            if math.log(rng.random()) < lp_new - lp_cur:
                mu, lp_cur = prop, lp_new
                accepted += 1
        elif block == 1:
            prop = kappa + rng.gauss(0, sd_kappa)
            lp_new = log_post(mu, prop, theta)
            if math.log(rng.random()) < lp_new - lp_cur:
                kappa, lp_cur = prop, lp_new
                accepted += 1
        else:
            for f in fids:
                prop = theta[f] + rng.gauss(0, sd_theta)
                old = theta[f]
                theta[f] = prop
                lp_new = log_post(mu, kappa, theta)
                if math.log(rng.random()) < lp_new - lp_cur:
                    lp_cur = lp_new
                    accepted += 1
                else:
                    theta[f] = old
        if it >= burn and (it - burn) % thin == 0:
            out["mu"].append(_inv_logit(mu))
            out["kappa"].append(math.exp(kappa))
            for f in fids:
                out["theta"][f].append(_inv_logit(theta[f]))
    out["acceptance_rate"] = accepted / float((draws + burn) * (2 + len(fids)) / 3.0)
    return out


def credible_interval(draws, level=0.95):
    s = sorted(draws)
    lo = s[int((1 - level) / 2 * len(s))]
    hi = s[min(len(s) - 1, int((1 + level) / 2 * len(s)))]
    return lo, hi


def facility_report(observations, draws=4000, burn=1000, seed=42,
                    exceedance=0.05, level=0.95):
    """Per-facility posterior fraud rate with shrinkage and a flag.

    A facility is flagged when the LOWER bound of its credible interval
    exceeds `exceedance` — i.e. we are (level)-confident its true flag rate
    is above the tolerance, not merely that the noisy point estimate is.
    """
    post = sample_posterior(observations, draws=draws, burn=burn, seed=seed)
    rows = []
    for fid, thetas in post["theta"].items():
        mean = sum(thetas) / len(thetas)
        lo, hi = credible_interval(thetas, level)
        rows.append({
            "facility": fid,
            "claims": observations[fid]["claims"],
            "flagged": observations[fid]["flagged"],
            "raw_rate": round(observations[fid]["flagged"] / observations[fid]["claims"], 4),
            "posterior_mean": round(mean, 4),
            "ci_low": round(lo, 4),
            "ci_high": round(hi, 4),
            "flag": lo > exceedance,
        })
    rows.sort(key=lambda r: r["posterior_mean"], reverse=True)
    mu = post["mu"]
    return {
        "national_rate_mean": round(sum(mu) / len(mu), 4),
        "national_rate_ci": [round(v, 4) for v in credible_interval(mu, level)],
        "pooled_kappa_mean": round(sum(post["kappa"]) / len(post["kappa"]), 2),
        "acceptance_rate": round(post["acceptance_rate"], 3),
        "facilities": rows,
    }
