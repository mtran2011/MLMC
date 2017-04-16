import abc
import itertools

class Stock(object):

    __metaclass__ = abc.ABCMeta

    def __init__(self, spot):
        self.spot = spot

    @abc.abstractmethod
    def find_volatilities(self, steps, *args):
        ''' Return the volatility values over time '''

    def walk_price(self, risk_free, price_steps, vol_steps=None):
        vol_steps = price_steps if vol_steps is None else vol_steps
        price = self.spot
        yield price

        for pstep, vol in itertools.izip(price_steps, self.find_volatilities(vol_steps)):
            det_term = risk_free * price
            sto_term = (vol**0.5) * pstep * price
            price += (det_term + sto_term)
            yield price


class ConstantVolatilityStock(Stock):

    def __init__(self, spot, vol):
        super(ConstantVolatilityStock, self).__init__(spot)
        self._vol = _vol

    def find_volatilities(self, steps, *args):
        return (self._vol for _ in steps)


class VariableVolatilityStock(Stock):

    def __init__(self, spot, base_vol, k, g):
        super(VariableVolatilityStock, self).__init__(spot)

        self._base_vol = base_vol
        self._k = k
        self._g = g

    def find_volatilities(self, steps, *args):
        vol = self._base_vol
        yield vol

        for s in steps:
            det_term = self._k * (self._base_vol - vol)
            sto_term = self._g * (vol**0.5) * s
            vol += (det_term + sto_term)
            yield vol