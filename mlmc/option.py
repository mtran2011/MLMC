from __future__ import division

import abc
import collections
import datetime
import functools
import itertools
import math
import numpy as np
import scipy.stats as ss

from mlmc import path, stock

class Option(object):

    __metaclass__ = abc.ABCMeta

    def __init__(self, assets, risk_free, expiry, is_call):
        self.assets = assets
        self.risk_free = risk_free
        self.expiry = expiry
        self.is_call = is_call

    @abc.abstractmethod
    def determine_payoff(self, *args, **kwargs):
        ''' Figure out the valuation of the option '''


class EuropeanStockOption(Option):

    def __init__(self, assets, risk_free, expiry, is_call, strike):
        if isinstance(assets, collections.Iterable):
            assets = assets[:1]
            if not isinstance(assets[0], stock.Stock):
                raise TypeError("Requires an underlying stock")
        elif isinstance(assets, stock.Stock):
            assets = [assets]
        else:
            raise TypeError("Requires an underlying stock")

        super(EuropeanStockOption, self).__init__(assets, risk_free, expiry, is_call)
        self.strike = strike

    def determine_payoff(self, final_spot, *args, **kwargs):
        v1, v2 = (final_spot, self.strike) if self.is_call else (self.strike, final_spot)
        return max(v1 - v2, 0)


class OptionSolver(object):

    __metaclass__ = abc.ABCMeta

    @abc.abstractmethod
    def solve_option_price(self, option, return_stats=False):
        return None


class AnalyticEuropeanStockOptionSolver(OptionSolver):

    def solve_option_price(self, option):
        underlying = option.assets[0]
        spot = underlying.spot
        vol = underlying.vol
        risk_free = option.risk_free
        expiry = option.expiry
        strike = option.strike

        log_diff = math.log(spot / strike)
        vt = 0.5 * vol**2
        denom = vol * math.sqrt(expiry)

        d1 = (log_diff + (risk_free + vt)*expiry) / denom
        d2 = (log_diff + (risk_free - vt)*expiry) / denom
        # F = spot * math.exp(expiry * risk_free)

        discount = math.exp(-risk_free * expiry)
        
        if option.is_call:
            S, d1, K, d2 = spot, d1, -strike, d2
        else:
            S, d1, K, d2 = -spot, -d1, strike, -d2
        
        return S * ss.norm.cdf(d1) + K * ss.norm.cdf(d2) * discount


class StatTracker(object):

    def __init__(self, discount):
        self.discount = discount
        self.count = 0
        self.total = 0
        self.sum_of_squares = 0
        self.initial_val = None

    @property
    def variance(self):
        if self.count in (0, 1):
            return float('inf')

        square_of_sum = self.total**2 / self.count
        variance = (self.sum_of_squares - square_of_sum) / (self.count - 1)
        return (self.discount * variance)

    @property
    def stdev(self):
        if self.count in (0, 1):
            return float('inf')

        return self.variance ** 0.5

    @property
    def mean(self):
        if self.count == 0:
            return float('nan')

        return self.discount * (self.total + self.initial_val*self.count) / self.count

    def add_sample(self, s):
        if self.initial_val is None:
            self.initial_val = s

        self.count += 1
        diff = s - self.initial_val
        self.total += diff
        self.sum_of_squares += diff**2

    def get_interval_length(self, z_score):
        if self.count == 0:
            return float('inf')

        return self.stdev * self.count**(-0.5) * z_score


class NaiveMCOptionSolver(OptionSolver):

    def __init__(self, max_interval_length, confidence_level=0.95, rng_creator=None):
        self.max_interval_length = max_interval_length
        self.confidence_level = confidence_level
        self.rng_creator = rng_creator

    @property
    def confidence_level(self):
        return self._confidence_level

    @confidence_level.setter
    def confidence_level(self, value):
        self._confidence_level = value
        self._z_score = ss.norm.ppf(1 - 0.5*(1-self.confidence_level))

    @property
    def z_score(self):
        return self._z_score

    def _simulate_paths(self, option, n_steps, discount):
        stat_tracker = StatTracker(discount)
        cnt = itertools.count()

        while next(cnt) < 10 or stat_tracker.get_interval_length(self.z_score) > self.max_interval_length:
            result = path.create_simple_path(option.assets,
                                             option.risk_free,
                                             option.expiry,
                                             n_steps,
                                             self.rng_creator)
            payoff = option.determine_payoff(*result)
            stat_tracker.add_sample(payoff)

        return stat_tracker

    def solve_option_price(self, option, return_stats=False):
        expiry = option.expiry
        risk_free = option.risk_free
        discount = math.exp(-risk_free * expiry)

        n_steps = int(math.floor(expiry / self.max_interval_length))

        tracker = self._simulate_paths(option, n_steps, discount)

        if return_stats:
            return tracker.mean, tracker.stdev, tracker.count, n_steps
        else:
            return tracker.mean


class LayeredMCOptionSolver(OptionSolver):

    def __init__(self,
                 target_mse,
                 rng_creator=None,
                 initial_n_levels=3, 
                 level_scaling_factor=4,
                 initial_n_paths=5000,
                 alpha=None,
                 beta=None,
                 gamma=None):
        self.target_mse = target_mse
        self.rng_creator = rng_creator
        self.initial_n_levels = max(initial_n_levels, 3)
        self.level_scaling_factor = max(level_scaling_factor, 2)
        self.initial_n_paths = initial_n_paths

        self._alpha = alpha
        self._beta = beta
        self._gamma = gamma

    def cost_determined(fn):
        @functools.wraps(fn)
        def wrapper(self, *args, **kwargs):
            d1 = datetime.datetime.now()
            res = fn(self, *args, **kwargs)
            d2 = datetime.datetime.now()

            delta = d2 - d1
            delta = delta.seconds + delta.microseconds*1e-6
            return delta, res

        return wrapper

    @cost_determined
    def _run_bottom_level(self, option, steps):
        result = path.create_simple_path(option.assets,
                                         option.risk_free,
                                         option.expiry,
                                         1,
                                         self.rng_creator)
        return option.determine_payoff(*result)

    @cost_determined
    def _run_upper_level(self, option, steps):
        result = path.create_layer_path(option.assets,
                                        option.risk_free,
                                        option.expiry,
                                        steps,
                                        self.rng_creator,
                                        K=self.level_scaling_factor)
        coarse, fine = zip(*result)
        payoff_coarse = option.determine_payoff(*coarse)
        payoff_fine = option.determine_payoff(*fine)

        return payoff_fine - payoff_coarse

    def _run_level(self, option, i, n, payoff_tracker, cost_tracker):
        steps = self.level_scaling_factor ** i

        if i == 0:
            fn = self._run_bottom_level
        else:
            fn = self._run_upper_level

        for _ in xrange(n):
            cost, payoff = fn(option, steps)
            cost_tracker.add_sample(cost)
            payoff_tracker.add_sample(payoff)

    def _determine_additional_n_values(self, trackers):
        overall = int(math.ceil(sum(
            (p.variance * c.mean)**0.5 
            for _, p, c in trackers
        ) / (self.target_mse**2)))

        return [
            max(0, int(math.ceil(overall * (p.variance * c.mean)**0.5)) - p.count)
            for _, p, c in trackers
        ]

    def _find_coefficients(self, payoff_trackers, cost_trackers):
        A = np.array([[i, 1] for i, _ in enumerate(payoff_trackers, 1)])

        if self._alpha:
            alpha = self._alpha
        else:
            x = np.array([[np.log2(p.mean)] for p in payoff_trackers])
            alpha = max(0.5, -np.linalg.lstsq(A, x)[0][0])

        if self._beta:
            beta = self._beta
        else:
            x = np.array([[np.log2(p.variance)] for p in payoff_trackers])
            beta = max(0.5, -np.linalg.lstsq(A, x)[0][0])

        if self._gamma:
            gamma = self._gamma
        else:
            x = np.array([[np.log2(p.mean)] for p in cost_trackers])
            gamma = np.linalg.lstsq(A, x)[0][0]

        return alpha, beta, gamma

    def _run_levels(self, option, discount):
        n_levels = self.initial_n_levels
        trackers = [
            (self.initial_n_paths, StatTracker(discount), StatTracker(1))
            for _ in xrange(n_levels)
        ]

        while sum(n for n, _, _ in trackers):
            for i, (n, payoff_tracker, cost_tracker) in enumerate(trackers):
                self._run_level(option, i, n, payoff_tracker, cost_tracker)

            addl_n_values = self._determine_additional_n_values(trackers)
            alpha, beta, gamma = self._find_coefficients(*zip(*((p, c) for (_, p, c) in trackers[1:])))

            trackers = [
                (addl_n, p, c)
                for addl_n, (_, p, c) in
                itertools.izip(addl_n_values, trackers)
            ]

            if all(n <= 0.01*p.count for n, p, _ in trackers):
                remaining_error = max(
                    (t.mean * 2**(alpha*i)) / (2**alpha - 1)
                    for i, (_, t, _) in enumerate(trackers[-2:], start=-2)
                )

                if remaining_error > (0.5**0.5) * self.target_mse:
                    guess_v = trackers[-1][1].variance  / (2^beta)
                    guess_c = trackers[-1][2].mean * (2 ** gamma)
                    term = (guess_v/guess_c) ** 0.5

                    base = sum((t.variance/c.cost)**0.5 for _, t, c in trackers)
                    base += term
                    guess_n = 2 * term * base / (self.target_mse**2)
                    trackers.append((guess_n, StatTracker(discount), StatTracker(1)))

        return sum(p.mean for _, p, _ in trackers)

    def solve_option_price(self, option):
        expiry = option.expiry
        risk_free = option.risk_free
        discount = math.exp(-risk_free * expiry)

        return self._run_levels(option, discount)
