"""Independent numeric checks and regression cases for indicator logic fixes."""
import math
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

import technical_analysis as ta


def candles(count=260):
    index = pd.date_range('2026-10-01 13:30Z', periods=count, freq='5min')
    close = 100 + np.arange(count)*.025 + np.sin(np.arange(count)/4)*.4
    frame = pd.DataFrame({'Open':close-.02, 'High':close+.5, 'Low':close-.5,
                          'Close':close, 'Volume':1000.}, index=index)
    frame.attrs = {'timeframe':'5m', 'exchange_timezone':'America/New_York'}
    return frame


def mirror(frame, anchor=300.):
    changed = frame.copy()
    changed['Open'], changed['Close'] = anchor-frame.Open, anchor-frame.Close
    changed['High'], changed['Low'] = anchor-frame.Low, anchor-frame.High
    if 'SUPPORT' in frame:
        changed['SUPPORT'], changed['RESISTANCE'] = anchor-frame.RESISTANCE, anchor-frame.SUPPORT
    return changed


def old_swings(direction):
    return ({'highs':[{'price':105.},{'price':106.}], 'lows':[{'price':96.},{'price':97.}]}
            if direction > 0 else
            {'highs':[{'price':195.},{'price':194.}], 'lows':[{'price':204.},{'price':203.}]})


class StructureLogicTests(unittest.TestCase):
    def structure(self, frame, swing_direction):
        with patch.object(ta,'_confirmed_swings',return_value=old_swings(swing_direction)), \
             patch.object(ta,'_fair_value_gaps',return_value=[]), patch.object(ta,'_order_blocks',return_value=[]):
            return ta._market_structure(frame,frame.iloc[-1])

    def test_new_bearish_break_overrides_old_bullish_swings(self):
        frame = pd.DataFrame({'Open':[100.], 'High':[100.5], 'Low':[97.5], 'Close':[98.],
                              'SUPPORT':[99.], 'RESISTANCE':[110.]},index=pd.DatetimeIndex(['2026-10-21T14:00Z']))
        result = self.structure(frame,1)
        self.assertEqual(result['event'],'BOS_BEARISH')
        self.assertEqual(result['bias'],'BEARISH')

    def test_mirrored_break_has_mirrored_direction(self):
        frame = pd.DataFrame({'Open':[100.], 'High':[100.5], 'Low':[97.5], 'Close':[98.],
                              'SUPPORT':[99.], 'RESISTANCE':[110.]},index=pd.DatetimeIndex(['2026-10-21T14:00Z']))
        bearish = self.structure(frame,1)
        bullish = self.structure(mirror(frame),-1)
        self.assertEqual((bearish['bias'],bullish['bias']),('BEARISH','BULLISH'))

    def test_bearish_sweep_overrides_old_bullish_swings(self):
        frame = pd.DataFrame({'Open':[101.], 'High':[112.], 'Low':[100.], 'Close':[105.],
                              'SUPPORT':[99.], 'RESISTANCE':[110.]},index=pd.DatetimeIndex(['2026-10-21T14:00Z']))
        result = self.structure(frame,1)
        self.assertEqual(result['event'],'LIQUIDITY_SWEEP_BEARISH')
        self.assertEqual(result['bias'],'BEARISH')

    def test_double_sweep_is_ambiguous_in_both_directions(self):
        frame = pd.DataFrame({'Open':[100.], 'High':[103.], 'Low':[97.], 'Close':[100.],
                              'SUPPORT':[99.], 'RESISTANCE':[101.]},index=pd.DatetimeIndex(['2026-10-21T14:00Z']))
        for direction in (-1,1):
            with self.subTest(direction=direction):
                result = self.structure(frame,direction)
                self.assertEqual(result['event'],'LIQUIDITY_SWEEP_BOTH')
                self.assertEqual(result['bias'],'NEUTRAL')

    def test_without_new_event_swings_remain_the_fallback(self):
        frame = pd.DataFrame({'Open':[100.], 'High':[101.], 'Low':[99.], 'Close':[100.],
                              'SUPPORT':[98.], 'RESISTANCE':[110.]},index=pd.DatetimeIndex(['2026-10-21T14:00Z']))
        for direction,expected in [(1,'BULLISH'),(-1,'BEARISH')]:
            self.assertEqual(self.structure(frame,direction)['bias'],expected)


class DirectionAndWarmupTests(unittest.TestCase):
    def indicator_vote(self, frame, name):
        result = ta.technical_at(frame)
        return next(component['signal'] for component in result['indicator_groups']['trend_structure']['components']
                    if component['name']==name)

    def test_equal_dmi_is_neutral_even_when_lagging_adx_is_high(self):
        frame = ta.build_indicator_frame(candles())
        frame.loc[frame.index[-1], ['ADX','PLUS_DI','MINUS_DI']] = [40.,20.,20.]
        self.assertEqual(self.indicator_vote(frame,'ADX/DMI'),0)

    def test_missing_dmi_is_not_a_sell_vote(self):
        frame = ta.build_indicator_frame(candles())
        frame.loc[frame.index[-1], ['ADX','PLUS_DI','MINUS_DI']] = [40.,np.nan,np.nan]
        self.assertEqual(self.indicator_vote(frame,'ADX/DMI'),0)

    def test_dmi_preserves_bullish_and_bearish_votes(self):
        frame = ta.build_indicator_frame(candles())
        for plus,minus,expected in [(30.,10.,1),(10.,30.,-1)]:
            frame.loc[frame.index[-1], ['ADX','PLUS_DI','MINUS_DI']] = [40.,plus,minus]
            self.assertEqual(self.indicator_vote(frame,'ADX/DMI'),expected)

    def test_zero_directional_movement_has_zero_adx_after_warmup(self):
        for spread in (0.,1.):
            with self.subTest(spread=spread):
                close = pd.Series([100.]*80)
                result = ta.calculate_adx(close+spread,close-spread,close)
                self.assertEqual(result['plus_di'].iloc[-1],0.)
                self.assertEqual(result['minus_di'].iloc[-1],0.)
                self.assertEqual(result['adx'].iloc[-1],0.)
                self.assertTrue(result['adx'].iloc[:26].isna().all())

    def test_missing_supertrend_is_not_displayed_as_bearish(self):
        frame = ta.build_indicator_frame(candles())
        frame.loc[frame.index[-1],['SUPERTREND_DIRECTION','SUPERTREND_LINE']] = np.nan
        self.assertEqual(ta.technical_at(frame)['supertrend']['direction'],'NEUTRAL')

    def test_zero_volatility_has_no_directional_supertrend_vote(self):
        frame = ta.build_indicator_frame(candles())
        frame.loc[frame.index[-1],['ATR','SUPERTREND_DIRECTION']] = [0.,1.]
        self.assertEqual(self.indicator_vote(frame,'Supertrend'),0)

    def test_mfi_requires_fourteen_observed_price_changes(self):
        frame = ta.build_indicator_frame(candles(25))
        self.assertTrue(frame.MFI14.iloc[:14].isna().all())
        self.assertTrue(pd.notna(frame.MFI14.iloc[14]))

    def test_mfi_matches_independent_window_sums(self):
        frame = candles(70)
        calculated = ta.build_indicator_frame(frame).MFI14
        typical = ((frame.High+frame.Low+frame.Close)/3).tolist()
        volume = frame.Volume.tolist()
        for end in range(14,len(frame)):
            plus=minus=0.
            for i in range(end-13,end+1):
                if typical[i]>typical[i-1]: plus += typical[i]*volume[i]
                elif typical[i]<typical[i-1]: minus += typical[i]*volume[i]
            expected = 100*plus/(plus+minus) if plus+minus else np.nan
            self.assertAlmostEqual(calculated.iloc[end],expected,places=10)


class VolumeContextTests(unittest.TestCase):
    def test_vwap_does_not_reset_at_utc_midnight_inside_exchange_day(self):
        frame = candles(2)
        frame.index = pd.DatetimeIndex(['2026-10-21T23:55Z','2026-10-22T00:00Z'])
        frame.loc[:, ['High','Low','Close']] = np.array([100.,200.])[:,None]
        self.assertEqual(ta.calculate_vwap(frame).tolist(),[100.,150.])

    def test_vwap_resets_at_exchange_midnight_when_utc_day_is_unchanged(self):
        frame = candles(2)
        frame.index = pd.DatetimeIndex(['2026-10-22T03:55Z','2026-10-22T04:00Z'])
        frame.loc[:, ['High','Low','Close']] = np.array([100.,200.])[:,None]
        self.assertEqual(ta.calculate_vwap(frame).tolist(),[100.,200.])

    def test_same_vwap_for_same_instants_in_different_timezones(self):
        frame = candles(20)
        local = frame.copy()
        local.index = local.index.tz_convert('America/New_York')
        self.assertTrue(np.allclose(ta.calculate_vwap(frame),ta.calculate_vwap(local),equal_nan=True))

    def test_profile_never_invents_levels_outside_the_traded_range(self):
        frame = candles(30)
        frame.loc[:,['Open','Close']] = 100.
        frame['High'],frame['Low'] = 100.001,99.999
        profile = ta._volume_profile(frame)
        for key in ('poc','value_area_low','value_area_high'):
            self.assertGreaterEqual(profile[key],frame.Low.min())
            self.assertLessEqual(profile[key],frame.High.max())

    def test_profile_with_one_price_reports_that_price(self):
        frame = candles(30)
        frame.loc[:,['Open','High','Low','Close']] = 100.
        profile = ta._volume_profile(frame)
        self.assertEqual([profile[key] for key in ('poc','value_area_low','value_area_high')],[100.,100.,100.])

    def test_value_area_grows_contiguously_from_poc(self):
        frame = candles(5)
        frame['Close'] = [100.5,101.5,102.5,103.5,104.5]
        frame['Open'] = frame.Close
        frame['High'],frame['Low'] = frame.Close+.5,frame.Close-.5
        frame['Volume'] = [45.,20.,10.,0.,25.]
        profile = ta._volume_profile(frame,bins=5)
        self.assertEqual(profile['poc'],100.5)
        self.assertEqual((profile['value_area_low'],profile['value_area_high']),(100.,103.))

    def test_zero_volume_remains_unavailable(self):
        frame = candles(30)
        frame['Volume'] = 0.
        self.assertIsNone(ta._volume_profile(frame)['poc'])
        self.assertTrue(ta.calculate_vwap(frame).isna().all())

    def test_daily_vwap_absence_is_not_mislabeled_as_missing_real_volume(self):
        frame = candles()
        frame.attrs['timeframe']='1D'
        warnings=ta.run_technical_analysis(frame)['warnings']
        self.assertFalse(any('Kein echtes Volumen' in warning for warning in warnings))
        self.assertTrue(any('VWAP' in warning for warning in warnings))


class ZoneTests(unittest.TestCase):
    def blocks_frame(self):
        frame=candles(31)
        frame.loc[:,['Open','Close']]=100.
        frame['High'],frame['Low']=101.,99.
        frame.loc[frame.index[19],['Open','Close']]=[100.,99.5]
        for i in range(20,31):
            close=102.5+(i-20)
            frame.loc[frame.index[i],['Open','High','Low','Close']]=[close-.25,close+.5,close-.5,close]
        frame.loc[frame.index[25],['Open','High','Low','Close']]=[107.5,108.,106.5,107.]
        frame['RESISTANCE']=frame.High.shift(1).rolling(20).max()
        frame['SUPPORT']=frame.Low.shift(1).rolling(20).min()
        return frame

    def test_same_orderblock_does_not_displace_other_unique_zones(self):
        frame=self.blocks_frame()
        blocks=ta._order_blocks(frame)
        self.assertEqual(len(blocks),2)
        self.assertEqual(len({(block['type'],block['created_at']) for block in blocks}),len(blocks))

    def test_first_valid_twenty_bar_break_is_not_skipped(self):
        frame=self.blocks_frame().iloc[:21]
        blocks=ta._order_blocks(frame)
        self.assertEqual(len(blocks),1)
        self.assertEqual(blocks[0]['created_at'],int(frame.index[19].timestamp()*1000))

    def test_orderblock_deduplication_is_symmetric_for_sell_side(self):
        blocks=ta._order_blocks(mirror(self.blocks_frame()))
        self.assertEqual(len(blocks),2)
        self.assertTrue(all(block['type']=='BEARISH' for block in blocks))

    def test_gaps_only_disappear_after_full_fill(self):
        frame=pd.DataFrame({'Open':[99.5,100.5,101.5,102.,100.5],
                            'High':[100.,103.,104.,103.,102.],
                            'Low':[99.,100.,101.,100.5,99.9],
                            'Close':[99.8,102.5,103.,101.5,100.], 'ATR':[1.]*5},
                           index=pd.date_range('2026-10-21T13:30Z',periods=5,freq='5min'))
        created=int(frame.index[2].timestamp()*1000)
        self.assertTrue(any(gap['created_at']==created for gap in ta._fair_value_gaps(frame.iloc[:4])))
        self.assertFalse(any(gap['created_at']==created for gap in ta._fair_value_gaps(frame)))


class IndependentCalculationTests(unittest.TestCase):
    def linear_frame(self):
        frame=candles()
        frame['Close']=100+np.arange(len(frame))*.2
        frame['Open']=frame.Close
        frame['High'],frame['Low']=frame.Close+1,frame.Close-1
        return frame

    def test_ema_and_sma_match_closed_form_for_linear_prices(self):
        frame=self.linear_frame()
        result=ta.build_indicator_frame(frame)
        for period in (9,20,50,100,200):
            alpha=2/(period+1)
            expected=frame.Close.iloc[-1]-.2*(1-alpha)/alpha*(1-(1-alpha)**(len(frame)-1))
            self.assertAlmostEqual(result[f'EMA{period}'].iloc[-1],expected,places=9)
            self.assertAlmostEqual(result[f'SMA{period}'].iloc[-1],frame.Close.iloc[-1]-.2*(period-1)/2,places=9)
            self.assertTrue(result[f'EMA{period}'].iloc[:period-1].isna().all())

    def test_macd_and_signal_match_independent_geometric_sums(self):
        frame=self.linear_frame()
        result=ta.calculate_macd(frame.Close)
        def expected_macd(t):
            return .2*(12.5*(1-(25/27)**t)-5.5*(1-(11/13)**t))
        end=len(frame)-1
        signal=(.8**(end-25))*expected_macd(25)+.2*sum(.8**(end-t)*expected_macd(t) for t in range(26,end+1))
        self.assertAlmostEqual(result['macd'].iloc[-1],expected_macd(end),places=10)
        self.assertAlmostEqual(result['signal'].iloc[-1],signal,places=10)
        self.assertAlmostEqual(result['hist'].iloc[-1],expected_macd(end)-signal,places=10)
        self.assertTrue(result['signal'].iloc[:33].isna().all())

    def test_ichimoku_cloud_uses_past_values_with_correct_displacement(self):
        result=ta.build_indicator_frame(self.linear_frame())
        close=result.Close.iloc[-1]
        self.assertAlmostEqual(result.ICHIMOKU_CONVERSION.iloc[-1],close-.2*4)
        self.assertAlmostEqual(result.ICHIMOKU_BASE.iloc[-1],close-.2*12.5)
        self.assertAlmostEqual(result.ICHIMOKU_SPAN_A.iloc[-1],close-.2*34.25)
        self.assertAlmostEqual(result.ICHIMOKU_SPAN_B.iloc[-1],close-.2*51.5)

    def test_roc_williams_and_keltner_have_known_values(self):
        result=ta.build_indicator_frame(self.linear_frame())
        self.assertAlmostEqual(result.ROC12.iloc[-1],100*(result.Close.iloc[-1]/result.Close.iloc[-13]-1))
        self.assertAlmostEqual(result.WILLIAMS_R.iloc[-1],-100/4.6)
        self.assertAlmostEqual(result.ATR.iloc[-1],2.)
        self.assertAlmostEqual(result.KELTNER_UPPER.iloc[-1]-result.KELTNER_MID.iloc[-1],4.)
        self.assertAlmostEqual(result.KELTNER_MID.iloc[-1]-result.KELTNER_LOWER.iloc[-1],4.)

    def test_stochastic_rsi_matches_independent_nested_windows(self):
        result=ta.build_indicator_frame(candles())
        rsi=result.RSI.tolist()
        raw={}
        for i in range(len(result)-5,len(result)):
            window=rsi[i-13:i+1]
            raw[i]=100*(rsi[i]-min(window))/(max(window)-min(window))
        k={i:sum(raw[j] for j in range(i-2,i+1))/3 for i in range(len(result)-3,len(result))}
        self.assertAlmostEqual(result.STOCH_RSI_K.iloc[-1],k[len(result)-1],places=10)
        self.assertAlmostEqual(result.STOCH_RSI_D.iloc[-1],sum(k.values())/3,places=10)

    def test_obv_and_cmf_distinguish_signed_volume_from_close_location(self):
        frame=self.linear_frame()
        result=ta.build_indicator_frame(frame)
        self.assertEqual(result.OBV.iloc[-1],(len(frame)-1)*1000.)
        self.assertAlmostEqual(result.CMF20.iloc[-1],0.)
        frame['High']=frame.Close
        frame['Low']=frame.Close-2
        self.assertAlmostEqual(ta.build_indicator_frame(frame).CMF20.iloc[-1],1.)
        frame['High']=frame.Close+2
        frame['Low']=frame.Close
        self.assertAlmostEqual(ta.build_indicator_frame(frame).CMF20.iloc[-1],-1.)

    def test_supertrend_known_trailing_bands_and_both_reversals(self):
        close=pd.Series([10.,11.,12.,11.,9.,8.,9.,12.])
        result=ta.calculate_supertrend(close+1,close-1,close,period=2,multiplier=1.)
        # Preserve the project's existing initial-up convention; verify the
        # subsequent trailing bands and reversals using hand-calculated values.
        expected=[np.nan,9.,10.,10.,11.5,10.25,10.25,8.9375]
        self.assertTrue(np.allclose(result['line'],expected,equal_nan=True))
        self.assertEqual(result['direction'].iloc[1:].tolist(),[1.,1.,1.,-1.,-1.,-1.,1.])

    def test_current_candle_cannot_redefine_its_own_breakout_levels(self):
        frame=candles()
        reference=ta.build_indicator_frame(frame)
        frame.loc[frame.index[-1],['High','Low']]=[1000.,1.]
        modified=ta.build_indicator_frame(frame)
        for name in ('SUPPORT','RESISTANCE','LOCAL_SUPPORT','LOCAL_RESISTANCE'):
            self.assertEqual(reference[name].iloc[-1],modified[name].iloc[-1])

    def test_wilder_smoothing_and_atr_have_known_values(self):
        result=ta._wilder(pd.Series([1.,2.,3.,4.,5.]),3)
        self.assertAlmostEqual(result.iloc[2],2.)
        self.assertAlmostEqual(result.iloc[3],8/3)
        self.assertAlmostEqual(result.iloc[4],31/9)
        atr=ta.calculate_atr(pd.Series([10.,12.,13.,12.]),pd.Series([8.,9.,11.,8.]),
                             pd.Series([9.,11.,12.,10.]),3)
        self.assertAlmostEqual(atr.iloc[2],7/3)
        self.assertAlmostEqual(atr.iloc[3],26/9)

    def test_rsi_known_gain_loss_example(self):
        result=ta.calculate_rsi(pd.Series([10.,11.,10.,12.,13.]),3)
        self.assertAlmostEqual(result.iloc[3],75.)
        self.assertAlmostEqual(result.iloc[4],100*9/11)

    def test_bollinger_uses_population_standard_deviation(self):
        bands=ta.calculate_bollinger_bands(pd.Series(np.arange(1.,21.)))
        self.assertEqual(bands['mid'].iloc[-1],10.5)
        self.assertAlmostEqual(bands['upper'].iloc[-1],10.5+2*math.sqrt(33.25))
        self.assertAlmostEqual(bands['lower'].iloc[-1],10.5-2*math.sqrt(33.25))

    def test_cci_matches_independent_mean_deviation(self):
        frame=candles(80)
        calculated=ta.build_indicator_frame(frame).CCI20.iloc[-1]
        sample=((frame.High+frame.Low+frame.Close)/3).iloc[-20:].tolist()
        mean=sum(sample)/20
        deviation=sum(abs(value-mean) for value in sample)/20
        self.assertAlmostEqual(calculated,(sample[-1]-mean)/(.015*deviation),places=9)

    def test_prefix_and_future_mutation_leave_past_unchanged(self):
        frame=candles()
        full=ta.build_indicator_frame(frame)
        prefix=ta.build_indicator_frame(frame.iloc[:210])
        pd.testing.assert_frame_equal(full.iloc[:210],prefix)
        changed=frame.copy()
        changed.iloc[210:,changed.columns.get_indexer(['Open','High','Low','Close'])]*=1.2
        later=ta.build_indicator_frame(changed)
        self.assertEqual(ta.technical_at(full,209),ta.technical_at(later,209))
        self.assertEqual(ta.technical_at(full,209),ta.technical_at(prefix))


if __name__=='__main__':
    unittest.main()
