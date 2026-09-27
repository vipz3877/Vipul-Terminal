import pandas as pd
import numpy as np

# ============================================================
# MASTER STRATEGY BACKTEST (MULTI-SIDE + 35-POINT BOUNDARY)
# Rules: 
# 1. Call-Writer Exiting (CE OI drops + CE Price rises)
# 2. Put Support Building (PE OI rises)
# 3. 35-Point Boundary Zone (Evaluates price pressure within the 35-pt threshold)
# ============================================================

print("Initializing Master Strategy Backtest Engine...")

np.random.seed(42)
rows = 600
timestamps = range(1700000000, 1700000000 + (rows * 300), 300)
spot_base = np.linspace(23000, 23600, rows) + np.random.normal(0, 12, rows)

# Call Option Stream Data
df_ce = pd.DataFrame({
    'timestamp': timestamps,
    'spot': spot_base,
    'ce_close': np.linspace(190, 260, rows) + np.random.normal(0, 4, rows),
    'ce_oi': np.linspace(600000, 380000, rows) + np.cumsum(np.random.normal(-800, 4000, rows))
})

# Put Option Stream Data (Opposite Side)
df_pe = pd.DataFrame({
    'pe_close': np.linspace(140, 175, rows) + np.random.normal(0, 4, rows),
    'pe_oi': np.linspace(350000, 620000, rows) + np.cumsum(np.random.normal(1500, 3500, rows))
})

# Combine into unified backtest dataframe
df_merged = pd.concat([df_ce, df_pe[['pe_close', 'pe_oi']]], axis=1)

# 1. Detect Call Writer Exiting
df_merged['CE_OI_Change'] = df_merged['ce_oi'].diff()
df_merged['CE_Price_Change'] = df_merged['ce_close'].diff()
call_writer_exiting = (df_merged['CE_OI_Change'] < 0) & (df_merged['CE_Price_Change'] > 0)

# 2. Detect Opposite Side Support (Put OI building)
df_merged['PE_OI_Change'] = df_merged['pe_oi'].diff()
put_support_building = df_merged['PE_OI_Change'] > 0

# 3. 35-Point Boundary Distance Filter
df_merged['Nearest_Strike'] = np.round(df_merged['spot'] / 100) * 100
df_merged['Strike_Distance'] = df_merged['spot'] - df_merged['Nearest_Strike']
boundary_pressure_zone = df_merged['Strike_Distance'].abs() <= 35

# 4. Spot Follow-Through Check (15 mins / 3 candles later)
df_merged['Future_Spot'] = df_merged['spot'].shift(-3)
df_merged['Spot_FollowThrough'] = df_merged['Future_Spot'] > df_merged['spot']

# Apply Master Strategy Rule Combination
master_signals = df_merged[call_writer_exiting & put_support_building & boundary_pressure_zone].copy()

total = len(master_signals)
if total > 0:
    wins = master_signals['Spot_FollowThrough'].sum()
    win_rate = (wins / total) * 100
    
    print(f"\n==================================================")
    print(f"--- MASTER STRATEGY BACKTEST RESULTS ---")
    print(f"Rule: Multi-Side Confirmation + 35-Pt Boundary Filter")
    print(f"Total Filtered Setup Events Found: {total}")
    print(f"Spot Followed Through Upwards (15 mins later): {wins}")
    print(f"Final Master Strategy Win Rate: {win_rate:.2f}%")
    print(f"==================================================")
    print("\nSample Master Setups:")
    print(master_signals[['spot', 'Strike_Distance', 'Future_Spot', 'Spot_FollowThrough']].head(5))
else:
    print("No events matched the master criteria.")