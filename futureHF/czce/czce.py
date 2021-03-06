from sys import path
path.append('/work/rqiao/HFdata')
from mewp.simulate.wrapper import PairAlgoWrapper
from mewp.simulate.runner import PairRunner
from mewp.simulate.report import Report
from mewp.math.simple import SimpleMoving
from mewp.util.clock import Clock
from mewp.data.order import OrderType
from mewp.reader.futuresqlite import SqliteReaderDce
from mewp.util.futures import get_day_db_path
from joblib import Parallel, delayed
import datetime
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import itertools
import os
DATA_PATH = '/work/rqiao/HFdata/dockfuture'
market = 'czce'

def get_contract_list(market, contract):
    return os.listdir(DATA_PATH + '/' + market + '/' + contract)

def get_position(contract, date, DATA_PATH):
    # create a dictionary, where date is the key
    try:
        reader = SqliteReaderDce(get_day_db_path(DATA_PATH, contract, date))
        raw = reader.read_tick()
        result = int(raw['Position'].tail(1))
    except Exception:
        result = 0
    return result

#input a date(format: '2016-01-01'), return the best pair of contract
def get_best_pair(date, market, contract):
    # remove contract which delivers in current month
    cont_list = get_contract_list(market, contract)
    year = date.split('-')[0][2:4]
    month = date.split('-')[1]
    try:
        cont_list.remove(contract + year + month)
    except ValueError:
        pass
    score = []
    for c in cont_list:
        score.append(get_position(c, date, DATA_PATH))
    if sum(score) == 0:
        return 0
    max_idx = np.argmax(score)
    score[max_idx] = 0
    second_max_idx = np.argmax(score)
    return (cont_list[max_idx], cont_list[second_max_idx])

## Simple moving average pair trading
# Max position within 1
class TestAlgo(PairAlgoWrapper):

    # called when algo param is set
    def param_updated(self):
        # make sure parent updates its param
        super(TestAlgo, self).param_updated()

        self.sd_coef = self.param['sd_coef']
        self.block = self.param['block']
        self.guard_coef = self.param['guard_coef']
        self.stop_win = self.param['stop_win']
        self.min_ticksize = self.pair.x.symbol.min_ticksize
        self.last_long_res = -999
        self.last_short_res = -999

    def on_daystart(self, date, info_x, info_y):
        # recreate rolling at each day start
        self.long_roll = SimpleMoving(size=self.param['rolling'])
        self.short_roll = SimpleMoving(size=self.param['rolling'])

    def on_dayend(self, date, info_x, info_y):
        pass

    def on_tick(self, multiple, contract, info):
        # skip if price_table doesnt have both, TODO fix this bug internally
        if len(self.price_table.table) < 2:
            return

        # get residuals and position
        long_res = self.pair.get_long_residual()
        short_res = self.pair.get_short_residual()
        pos = self.position_y()

        if pos == -1:
            if long_res + self.last_short_res >= max(self.stop_win, 2*self.long_roll.sd) * self.min_ticksize:
                self.long_y(y_qty = 1)
                return

        # stop long position
        if pos == 1:
            if short_res + self.last_long_res >= max(self.stop_win, 2*self.short_roll.sd) * self.min_ticksize:
                self.short_y(y_qty = 1)
                return

        # get ask-bid spread cost
        spreadcost = self.price_table.get_ask(self.pair.x) - self.price_table.get_bid(self.pair.x) + self.price_table.get_ask(self.pair.y) - self.price_table.get_bid(self.pair.y)
        if spreadcost < 0:
            raise Exception

        # action only when unblocked: bock size < rolling queue size
        if self.long_roll.queue.qsize() > self.block:
            # long when test long_res > roll.mean+sd_coef*roll.sd
            # if self.long_roll.test_sigma(long_res, self.sd_coef):

            if long_res > self.long_roll.mean + max( self.sd_coef*self.long_roll.sd,(self.guard_coef*self.min_ticksize + spreadcost)) :
                # only long when position is 0 or -1
                if pos <= 0:
                    self.long_y(y_qty=1)
                    self.last_long_res = long_res
                    return


            # short when test short_res > roll.mean+sd_coef*roll.sd
            #elif self.short_roll.test_sigma(short_res, self.sd_coef):
            elif short_res > self.short_roll.mean + max( self.sd_coef*self.short_roll.sd,(self.guard_coef*self.min_ticksize + spreadcost)) :
                 # only short when position is 0 or 1
                if pos >= 0:
                    self.short_y(y_qty=1)
                    self.last_short_res = short_res
                    return
        # update rolling
        self.long_roll.add(long_res)
        self.short_roll.add(short_res)

product_list = os.listdir(DATA_PATH + '/' + market)
start_date = '20160115'
end_date = '20160228'
dates = pd.date_range(start_date,end_date)
date_list = [day.strftime("%Y-%m-%d") for day in dates]

## Loop throgh all products in the market and find the best pair in the first day
for product in product_list:
    best_pair = get_best_pair(date_list[0], market = market, contract = product)
    print best_pair
    if best_pair == 0:
        continue
    algo = { 'class': TestAlgo }
    algo['param'] = {'x': best_pair[0],
                     'y': best_pair[1],
                     'a': 1,
                     'b': 0,
                     'rolling': 8000,
                     'sd_coef': 6.0,
                     'guard_coef': 1,
                     'stop_win': 1,
                     'bucket_size': 500,
                     'block': 100}
    settings = { 'date': date_list,
                'path': DATA_PATH,
                'tickset': 'top',
                'algo': algo}
    runner = PairRunner(settings)

    rolling_list = range(1000,5000,1000)
    sd_coef_list = np.arange(2,6,1)
    guard_list = np.arange(0.5,3.5,0.5)
    stop_win_list = np.arange(1,5,1)
    pars = list(itertools.product(rolling_list, sd_coef_list, guard_list, stop_win_list))
    def run_simulation(p):
        runner.run(algo_param={'rolling': p[0], 'sd_coef': p[1], 'guard_coef': p[2], 'stop_win': p[3]})
        report = Report(runner)
        return report
    num_cores = 20
    results = Parallel(n_jobs=num_cores)(delayed(run_simulation)(p) for p in pars)
    submit = pd.DataFrame({"rolling": [p[0] for p in pars],
                           "sd_coef": [p[1] for p in pars],
                           "guard_coef": [p[2] for p in pars],
                           'stop_win': [p[3] for p in pars],
                           "sharpe_ratio": [r.get_sharpie_ratio() for r in results],
                           "win_ratio": [r.get_win_ratio() for r in results],
                           "PNL": [list(r.get_pnl()['pnl'])[-1] for r in results],
                           "num_orders": [sum(r.get_daily_order_count()['order_count']) for r in results]})
    submit.to_csv('./out/' + product + '_' + start_date + '_' + end_date + '_guard_stopwin.csv')
