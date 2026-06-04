"""Built-in strategy registry helpers.

The community edition keeps heavy AI model dependencies optional. Importing the
``app.strategies`` package must therefore not require torch/Kairos just to load
ordinary CTA, grid, arbitrage, or paper broker examples.
"""

from app.strategies.contract_daily_target_scalp_strategy import (
    ContractDailyTargetScalpStrategy,
)
from app.strategies.contract_donchian_adx_breakout_strategy import (
    ContractDonchianAdxBreakoutStrategy,
)
from app.strategies.contract_ema_atr_scalp_strategy import (
    ContractEmaAtrScalpStrategy,
)
from app.strategies.contract_fvg_liquidity_sweep_strategy import (
    ContractFvgLiquiditySweepStrategy,
)
from app.strategies.contract_fvg_ob_strategy import (
    ContractFvgObStrategy,
)
from app.strategies.contract_heikin_ashi_trend_strategy import (
    ContractHeikinAshiTrendStrategy,
)
from app.strategies.contract_liquidity_sweep_strategy import (
    ContractLiquiditySweepStrategy,
)
from app.strategies.contract_low_leverage_trend_strategy import (
    ContractLowLeverageTrendStrategy,
)
from app.strategies.contract_martingale_grid_strategy import (
    ContractMartingaleGridStrategy,
)
from app.strategies.contract_order_flow_breakout_strategy import (
    ContractOrderFlowBreakoutStrategy,
)
from app.strategies.contract_shared_martingale_grid_strategy import (
    ContractSharedMartingaleGridStrategy,
)
from app.strategies.contract_supertrend_swing_breakout_strategy import (
    ContractSupertrendSwingBreakoutStrategy,
)
from app.strategies.contract_vwap_volume_profile_strategy import (
    ContractVwapVolumeProfileStrategy,
)
from app.strategies.cta_trend_following_strategy import (
    CtaTrendFollowingStrategy,
)
from app.strategies.dynamic_cta_trend_following_strategy import (
    DynamicCtaTrendFollowingStrategy,
)
from app.strategies.funding_rate_arbitrage_strategy import (
    FundingRateArbitrageStrategy,
)
from app.strategies.grid_trading_strategy import (
    GridTradingStrategy,
)
from app.strategies.okx_funding_arbitrage_strategy import (
    OkxFundingArbitrageStrategy,
)
from app.strategies.spot_cta_trend_following_strategy import (
    SpotCtaTrendFollowingStrategy,
)

try:
    from app.strategies.kairos_30m_horizon_dca_strategy import (
        Kairos30mHorizonDcaStrategy,
    )
except ImportError:
    Kairos30mHorizonDcaStrategy = None

try:
    from app.strategies.kairos_superpnl_cost_aware_strategy import (
        KairosSuperPnLCostAwareStrategy,
    )
except ImportError:
    KairosSuperPnLCostAwareStrategy = None


STRATEGY_CLASSES = {
    "okx_funding_arbitrage": OkxFundingArbitrageStrategy,
    "spot_cta_trend_following": SpotCtaTrendFollowingStrategy,
    "cta_trend_following": CtaTrendFollowingStrategy,
    "contract_heikin_ashi_trend": ContractHeikinAshiTrendStrategy,
    "contract_fvg_ob_1h_100u": ContractFvgObStrategy,
    "contract_liquidity_sweep_1h_bch_100u": ContractLiquiditySweepStrategy,
    "contract_supertrend_swing_breakout_sol_15m_100u": ContractSupertrendSwingBreakoutStrategy,
    "contract_vwap_volume_profile_btc_eth_sol_1h_100u": ContractVwapVolumeProfileStrategy,
    "contract_fvg_liquidity_sweep_btc_eth_sol_15m_100u": ContractFvgLiquiditySweepStrategy,
    "contract_order_flow_breakout_btc_eth_sol_5m_100u": ContractOrderFlowBreakoutStrategy,
    "contract_grass_1h_donchian_adx_100u": ContractDonchianAdxBreakoutStrategy,
    "contract_low_leverage_trend_1h_eth_10u": ContractLowLeverageTrendStrategy,
    "contract_daily_target_scalp_10u": ContractDailyTargetScalpStrategy,
    "contract_ema_atr_scalp": ContractEmaAtrScalpStrategy,
    "dynamic_cta_trend_following_top15": DynamicCtaTrendFollowingStrategy,
    "grid_trading": GridTradingStrategy,
    "contract_martingale_grid": ContractMartingaleGridStrategy,
    "contract_shared_martingale_grid": ContractSharedMartingaleGridStrategy,
    "funding_rate_arbitrage": FundingRateArbitrageStrategy,
}

if Kairos30mHorizonDcaStrategy is not None:
    STRATEGY_CLASSES.update(
        {
            "kairos_30m_horizon_dca": Kairos30mHorizonDcaStrategy,
            "kairos_30m_horizon_dca_5m": Kairos30mHorizonDcaStrategy,
            "kairos_30m_horizon_dca_10m": Kairos30mHorizonDcaStrategy,
            "kairos_3m_horizon_hft": Kairos30mHorizonDcaStrategy,
            "kairos_30m_horizon_dca_flat_half": Kairos30mHorizonDcaStrategy,
        }
    )

if KairosSuperPnLCostAwareStrategy is not None:
    STRATEGY_CLASSES["kairos_superpnl_cost_aware"] = KairosSuperPnLCostAwareStrategy

__all__ = [
    "STRATEGY_CLASSES",
    "CtaTrendFollowingStrategy",
    "ContractDailyTargetScalpStrategy",
    "ContractDonchianAdxBreakoutStrategy",
    "ContractEmaAtrScalpStrategy",
    "ContractFvgLiquiditySweepStrategy",
    "ContractFvgObStrategy",
    "ContractHeikinAshiTrendStrategy",
    "ContractLiquiditySweepStrategy",
    "ContractLowLeverageTrendStrategy",
    "ContractMartingaleGridStrategy",
    "ContractOrderFlowBreakoutStrategy",
    "ContractSharedMartingaleGridStrategy",
    "ContractSupertrendSwingBreakoutStrategy",
    "ContractVwapVolumeProfileStrategy",
    "DynamicCtaTrendFollowingStrategy",
    "FundingRateArbitrageStrategy",
    "GridTradingStrategy",
    "OkxFundingArbitrageStrategy",
    "SpotCtaTrendFollowingStrategy",
]

if Kairos30mHorizonDcaStrategy is not None:
    __all__.append("Kairos30mHorizonDcaStrategy")

if KairosSuperPnLCostAwareStrategy is not None:
    __all__.append("KairosSuperPnLCostAwareStrategy")
