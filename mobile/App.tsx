import React, { useCallback, useEffect, useState } from 'react';
import {
  View,
  Text,
  ScrollView,
  StyleSheet,
  StatusBar,
  Pressable,
  RefreshControl,
  ActivityIndicator,
  TextInput,
} from 'react-native';

const API_URL =
  'https://symmetrical-meme-55qpjvggv5pcp7qj-8000.app.github.dev';

const GREEN = '#00D6A0';
const BG = '#080D18';
const CARD = '#121B2B';
const MUTED = '#8492A6';

type Account = {
  starting_balance: number;
  cash: number;
  realized_pnl: number;
  unrealized_pnl: number;
  equity: number;
  risk_per_trade_percent: number;
  maximum_risk_per_trade: number;
  open_risk: number;
  maximum_total_open_risk: number;
  open_positions_count: number;
};

type Position = {
  trade_id: string;
  instrument_key: string;
  symbol: string;
  exchange: string;
  quantity_unit: string;
  side: string;
  entry_price: number;
  current_price: number;
  stop_loss: number;
  take_profit: number;
  quantity: number;
  risk_amount: number;
  status: string;
};

type Instrument = {
  instrument_key: string;
  name: string;
  trading_symbol: string;
  exchange: string;
  segment: string;
  instrument_type: string;
  lot_size: number;
  tick_size: number;
};

type Order = {
  trade_id?: string;
  symbol?: string;
  side?: string;
  type?: string;
  status?: string;
  realized_pnl?: number;
  opened_at?: string;
  closed_at?: string;
};

const MARKET_WATCH_INSTRUMENTS = [
  { name: 'NIFTY 50', exchange: 'NSE', instrument_key: 'NSE_INDEX|Nifty 50' },
  { name: 'BANK NIFTY', exchange: 'NSE', instrument_key: 'NSE_INDEX|Nifty Bank' },
  { name: 'SENSEX', exchange: 'BSE', instrument_key: 'BSE_INDEX|SENSEX' },
  { name: 'NHPC', exchange: 'NSE', instrument_key: 'NSE_EQ|INE848E01016' },
  { name: 'GOLD FUTURES', exchange: 'MCX', instrument_key: 'MCX_FO|495213' },
  { name: 'CRUDE OIL FUTURES', exchange: 'MCX', instrument_key: 'MCX_FO|573422' },
];

type MarketQuote = {
  instrument_key: string;
  name: string;
  exchange: string;
  last_price?: number;
  net_change?: number;
  volume?: number;
  timestamp?: string;
  error?: string;
};

type Safety = {
  paper_trading_only: boolean;
  live_order_execution: boolean;
  broker_order_api_enabled: boolean;
};

const money = (value = 0) =>
  '₹' + Number(value).toLocaleString('en-IN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });

export default function App() {
  const [account, setAccount] = useState<Account | null>(null);
  const [positions, setPositions] = useState<Position[]>([]);
  const [orders, setOrders] = useState<Order[]>([]);
  const [safety, setSafety] = useState<Safety | null>(null);
  const [marketQuotes, setMarketQuotes] = useState<MarketQuote[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [exchange, setExchange] = useState<'NSE' | 'MCX'>('NSE');
const [searchQuery, setSearchQuery] = useState('');
const [instruments, setInstruments] = useState<Instrument[]>([]);
const [selectedInstrument, setSelectedInstrument] =
  useState<Instrument | null>(null);

const [side, setSide] = useState<'BUY' | 'SELL'>('BUY');
const [entryPrice, setEntryPrice] = useState('');
const [stopLoss, setStopLoss] = useState('');
const [takeProfit, setTakeProfit] = useState('');

const [searching, setSearching] = useState(false);
const [submittingTrade, setSubmittingTrade] = useState(false);
const [tradeMessage, setTradeMessage] = useState('');
const loadMarketWatch = useCallback(async () => {
  const results = await Promise.all(
    MARKET_WATCH_INSTRUMENTS.map(async (instrument) => {
      try {
        const response = await fetch(
          `${API_URL}/api/market/quote?instrument_key=${encodeURIComponent(
            instrument.instrument_key
          )}`
        );

        const data = await response.json();

        if (!response.ok) {
          throw new Error(
            data.detail || data.error || 'Quote unavailable'
          );
        }

        const quote = Object.values(data.market_data || {})[0] as any;

        if (!quote || typeof quote.last_price !== 'number') {
          throw new Error('No quote data received');
        }

        return {
          ...instrument,
          last_price: quote.last_price,
          net_change: quote.net_change ?? 0,
          volume: quote.volume ?? 0,
          timestamp: quote.timestamp || '',
        };
      } catch (err) {
        return {
          ...instrument,
          error: err instanceof Error ? err.message : 'Quote unavailable',
        };
      }
    })
  );

  setMarketQuotes(results);
}, []);

  const loadDashboard = useCallback(async () => {
    try {
      setError('');

      const [accountRes, positionsRes, ordersRes, safetyRes] =
        await Promise.all([
          fetch(`${API_URL}/api/paper/account`),
          fetch(`${API_URL}/api/paper/positions`),
          fetch(`${API_URL}/api/paper/orders`),
          fetch(`${API_URL}/api/system/safety`),
        ]);

      if (
        !accountRes.ok ||
        !positionsRes.ok ||
        !ordersRes.ok ||
        !safetyRes.ok
      ) {
        throw new Error('One or more backend endpoints returned an error.');
      }

      const [accountData, positionsData, ordersData, safetyData] =
        await Promise.all([
          accountRes.json(),
          positionsRes.json(),
          ordersRes.json(),
          safetyRes.json(),
        ]);

      setAccount(accountData);
      setPositions(positionsData);
      setOrders(ordersData);
      setSafety(safetyData);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : 'Unable to connect to backend.'
      );
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
  loadDashboard();
  loadMarketWatch();

  const interval = setInterval(() => {
    loadMarketWatch();
  }, 15000);

  return () => clearInterval(interval);
}, [loadDashboard, loadMarketWatch]);

  const refresh = () => {
  setRefreshing(true);
  loadDashboard();
  loadMarketWatch();
};

  const backendConnected = account !== null && safety !== null;
  const paperSafe =
    safety?.paper_trading_only === true &&
    safety?.live_order_execution === false &&
    safety?.broker_order_api_enabled === false;

     const searchInstruments = async () => {
    const query = searchQuery.trim();

    if (!query) {
      setTradeMessage('Enter an instrument name or symbol.');
      return;
    }

    try {
      setSearching(true);
      setTradeMessage('');
      setSelectedInstrument(null);
      setInstruments([]);

      const segment = exchange === 'NSE' ? 'EQ' : 'COMM';
      const instrumentType = exchange === 'NSE' ? 'EQ' : 'FUT';

      const url =
        `${API_URL}/api/market/instruments/search?query=${encodeURIComponent(query)}` +
        `&exchange=${exchange}&segment=${segment}&instrument_type=${instrumentType}`;

      const response = await fetch(url);
      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.detail || data.error || 'Instrument search failed.');
      }

      setInstruments(
        Array.isArray(data.instruments) ? data.instruments : []
      );

      if (!data.instruments?.length) {
        setTradeMessage('No matching instruments found.');
      }
    } catch (err) {
      setTradeMessage(
        err instanceof Error ? err.message : 'Search failed.'
      );
    } finally {
      setSearching(false);
    }
  };

  const submitPaperTrade = async () => {
    if (!selectedInstrument) {
      setTradeMessage('Select an instrument first.');
      return;
    }

    const entry = Number(entryPrice);
    const stop = Number(stopLoss);
    const target = Number(takeProfit);

    if (![entry, stop, target].every(
      value => Number.isFinite(value) && value > 0
    )) {
      setTradeMessage('Enter valid positive prices for all three fields.');
      return;
    }

    try {
      setSubmittingTrade(true);
      setTradeMessage('');

      const response = await fetch(`${API_URL}/api/paper/trades`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          instrument_key: selectedInstrument.instrument_key,
          side,
          entry_price: entry,
          stop_loss: stop,
          take_profit: target,
        }),
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.detail || data.error || 'Paper trade rejected.');
      }

      setTradeMessage(
        `PAPER TRADE CREATED: ${data.trade.symbol} | ` +
        `${data.trade.quantity} ${data.trade.quantity_unit} | ` +
        `Risk: ₹${Number(data.trade.risk_amount).toFixed(2)}`
      );

      setEntryPrice('');
      setStopLoss('');
      setTakeProfit('');
      setSelectedInstrument(null);
      setInstruments([]);

      await loadDashboard();
    } catch (err) {
      setTradeMessage(
        err instanceof Error ? err.message : 'Unable to create paper trade.'
      );
    } finally {
      setSubmittingTrade(false);
    }
  };
  return (
    <View style={styles.container}>
      <StatusBar barStyle="light-content" />

      <ScrollView
        contentContainerStyle={styles.content}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={refresh}
            tintColor={GREEN}
          />
        }
      >
        <View style={styles.header}>
          <View>
            <Text style={styles.brand}>ALGOPILOT</Text>
            <Text style={styles.subtitle}>
              INDIA • ALGORITHMIC TRADING
            </Text>
          </View>
          <Text style={styles.paper}>PAPER MODE</Text>
        </View>

        <View style={styles.connectionRow}>
          <Text style={styles.muted}>Backend API</Text>
          <Text style={backendConnected ? styles.good : styles.bad}>
            {backendConnected ? '● CONNECTED' : '● NOT CONNECTED'}
          </Text>
        </View>

        {error ? (
          <View style={styles.errorCard}>
            <Text style={styles.errorText}>{error}</Text>
            <Text style={styles.muted}>
              Check that your Codespaces backend is running and port 8000 is
              accessible.
            </Text>
          </View>
        ) : null}

        <Text style={styles.section}>PORTFOLIO OVERVIEW</Text>

        <View style={styles.balanceCard}>
          <Text style={styles.muted}>Paper account equity</Text>
          {loading && !account ? (
            <ActivityIndicator color={GREEN} />
          ) : (
            <>
              <Text style={styles.balance}>
                {money(account?.equity)}
              </Text>
              <Text
                style={
                  (account?.realized_pnl || 0) +
                    (account?.unrealized_pnl || 0) >=
                  0
                    ? styles.positive
                    : styles.negative
                }
              >
                {money(
                  (account?.realized_pnl || 0) +
                    (account?.unrealized_pnl || 0)
                )}{' '}
                total P&L
              </Text>
              <Text style={styles.muted}>
                Cash: {money(account?.cash)} • Demo funds only
              </Text>
            </>
          )}
        </View>

        <View style={styles.grid}>
          <View style={styles.stat}>
            <Text style={styles.muted}>Realized P&L</Text>
            <Text style={styles.statValue}>
              {money(account?.realized_pnl)}
            </Text>
          </View>

          <View style={styles.stat}>
            <Text style={styles.muted}>Unrealized P&L</Text>
            <Text style={styles.statValue}>
              {money(account?.unrealized_pnl)}
            </Text>
          </View>

          <View style={styles.stat}>
            <Text style={styles.muted}>Open positions</Text>
            <Text style={styles.statValue}>
              {account?.open_positions_count ?? 0}
            </Text>
          </View>

          <View style={styles.stat}>
            <Text style={styles.muted}>Risk per trade</Text>
            <Text style={styles.statValue}>
              {account?.risk_per_trade_percent ?? 0.5}%
            </Text>
          </View>
        </View>

        <Text style={styles.section}>RISK MANAGEMENT</Text>
        <View style={styles.engine}>
          <InfoRow
            label="Maximum risk per trade"
            value={money(account?.maximum_risk_per_trade)}
          />
          <InfoRow
            label="Current open risk"
            value={money(account?.open_risk)}
          />
          <InfoRow
            label="Maximum combined risk"
            value={money(account?.maximum_total_open_risk)}
          />
        </View>

                <Text style={styles.section}>NEW PAPER TRADE</Text>

        <View style={styles.formCard}>
          <Text style={styles.fieldLabel}>EXCHANGE</Text>

          <View style={styles.choiceRow}>
            {(['NSE', 'MCX'] as const).map((item) => (
              <Pressable
                key={item}
                style={[
                  styles.choiceButton,
                  exchange === item && styles.choiceSelected,
                ]}
                onPress={() => {
                  setExchange(item);
                  setSearchQuery('');
                  setInstruments([]);
                  setSelectedInstrument(null);
                  setTradeMessage('');
                }}
              >
                <Text style={exchange === item ? styles.choiceTextSelected : styles.choiceText}>
                  {item}
                </Text>
              </Pressable>
            ))}
          </View>

          <Text style={styles.fieldLabel}>SEARCH INSTRUMENT</Text>

          <TextInput
            style={styles.textInput}
            value={searchQuery}
            onChangeText={setSearchQuery}
            placeholder={exchange === 'NSE' ? 'Example: NHPC, RELIANCE' : 'Example: GOLD, SILVER'}
            placeholderTextColor={MUTED}
            autoCapitalize="characters"
            returnKeyType="search"
            onSubmitEditing={searchInstruments}
          />

          <Pressable
            style={[styles.actionButton, searching && styles.disabledButton]}
            onPress={searchInstruments}
            disabled={searching}
          >
            {searching ? (
              <ActivityIndicator color="#FFFFFF" />
            ) : (
              <Text style={styles.actionButtonText}>SEARCH INSTRUMENTS</Text>
            )}
          </Pressable>

          {instruments.map((instrument) => (
            <Pressable
              key={instrument.instrument_key}
              style={[
                styles.instrumentOption,
                selectedInstrument?.instrument_key === instrument.instrument_key &&
                  styles.choiceSelected,
              ]}
              onPress={() => {
                setSelectedInstrument(instrument);
                setTradeMessage('');
              }}
            >
              <Text style={styles.symbol}>
                {instrument.trading_symbol}
              </Text>
              <Text style={styles.muted}>
                {instrument.name} • {instrument.exchange} • Lot: {instrument.lot_size}
              </Text>
            </Pressable>
          ))}

          {selectedInstrument && (
            <View style={styles.selectedInstrument}>
              <Text style={styles.good}>SELECTED INSTRUMENT</Text>
              <Text style={styles.symbol}>
                {selectedInstrument.trading_symbol}
              </Text>
              <Text style={styles.muted}>
                {selectedInstrument.instrument_key}
              </Text>
              <Text style={styles.muted}>
                Lot size: {selectedInstrument.lot_size} • Tick size: {selectedInstrument.tick_size}
              </Text>
            </View>
          )}

          <Text style={styles.fieldLabel}>TRADE SIDE</Text>

          <View style={styles.choiceRow}>
            {(['BUY', 'SELL'] as const).map((item) => (
              <Pressable
                key={item}
                style={[
                  styles.choiceButton,
                  side === item && styles.choiceSelected,
                ]}
                onPress={() => setSide(item)}
              >
                <Text style={side === item ? styles.choiceTextSelected : styles.choiceText}>
                  {item}
                </Text>
              </Pressable>
            ))}
          </View>

          <Text style={styles.fieldLabel}>ENTRY PRICE (₹)</Text>
          <TextInput
            style={styles.textInput}
            value={entryPrice}
            onChangeText={setEntryPrice}
            placeholder="Enter entry price"
            placeholderTextColor={MUTED}
            keyboardType="decimal-pad"
          />

          <Text style={styles.fieldLabel}>STOP-LOSS (₹)</Text>
          <TextInput
            style={styles.textInput}
            value={stopLoss}
            onChangeText={setStopLoss}
            placeholder="Enter stop-loss price"
            placeholderTextColor={MUTED}
            keyboardType="decimal-pad"
          />

          <Text style={styles.fieldLabel}>TARGET PRICE (₹)</Text>
          <TextInput
            style={styles.textInput}
            value={takeProfit}
            onChangeText={setTakeProfit}
            placeholder="Enter target price"
            placeholderTextColor={MUTED}
            keyboardType="decimal-pad"
          />

          <Pressable
            style={[
              styles.submitButton,
              (!selectedInstrument || submittingTrade) && styles.disabledButton,
            ]}
            onPress={submitPaperTrade}
            disabled={!selectedInstrument || submittingTrade}
          >
            {submittingTrade ? (
              <ActivityIndicator color="#FFFFFF" />
            ) : (
              <Text style={styles.actionButtonText}>CREATE PAPER TRADE</Text>
            )}
          </Pressable>

          {tradeMessage !== '' && (
            <View style={styles.tradeMessage}>
              <Text style={styles.tradeMessageText}>{tradeMessage}</Text>
            </View>
          )}

          <Text style={styles.formNote}>
            SIMULATION ONLY • No live broker orders • Quantity and risk calculated by backend
          </Text>
        </View>

        <Text style={styles.section}>OPEN POSITIONS</Text>

        {positions.length === 0 ? (
          <View style={styles.empty}>
            <Text style={styles.muted}>No open paper positions</Text>
          </View>
        ) : (
          positions.map((position) => (
            <View style={styles.tradeCard} key={position.trade_id}>
              <View style={styles.tradeHeader}>
                <Text style={styles.symbol}>{position.symbol}</Text>
                <Text style={styles.good}>{position.side}</Text>
              </View>
              <InfoRow
                label="Entry / Current"
                value={`${position.entry_price} / ${position.current_price}`}
              />
              <InfoRow
                label="Quantity"
                value={String(position.quantity)}
              />
              <InfoRow
                label="Stop-loss / Target"
                value={`${position.stop_loss} / ${position.take_profit}`}
              />
              <InfoRow
                label="Planned risk"
                value={money(position.risk_amount)}
              />
            </View>
          ))
        )}

        <Text style={styles.section}>RECENT PAPER ORDERS</Text>

        {orders.length === 0 ? (
          <View style={styles.empty}>
            <Text style={styles.muted}>No paper orders yet</Text>
          </View>
        ) : (
          orders.slice().reverse().slice(0, 10).map((order, index) => (
            <View
              style={styles.orderRow}
              key={`${order.trade_id || 'order'}-${index}`}
            >
              <View>
                <Text style={styles.symbol}>
                  {order.symbol || 'Unknown'}
                </Text>
                <Text style={styles.muted}>
                  {order.type || order.side || 'PAPER ORDER'}
                </Text>
              </View>
              <Text style={styles.waiting}>
                {order.status || 'RECORDED'}
              </Text>
            </View>
          ))
        )}

        <Text style={styles.section}>MARKET WATCH</Text>

        {marketQuotes.map((item) => (
          <View key={item.instrument_key} style={styles.orderRow}>
            <View style={{ flex: 1 }}>
              <Text style={styles.symbol}>{item.name}</Text>
              <Text style={styles.muted}>
                {item.exchange} • UPSTOX
              </Text>
              {item.timestamp ? (
                <Text style={styles.muted}>
                  Updated: {new Date(item.timestamp).toLocaleTimeString('en-IN')}
                </Text>
              ) : null}
              {item.error ? (
                <Text style={styles.waiting}>{item.error}</Text>
              ) : null}
            </View>

            <View style={{ alignItems: 'flex-end' }}>
              <Text style={styles.price}>
                {item.last_price !== undefined
                  ? money(item.last_price)
                  : 'Unavailable'}
              </Text>

              {item.last_price !== undefined ? (
                <Text
                  style={{
                    color: (item.net_change ?? 0) >= 0
                      ? GREEN
                      : '#FF5C70',
                  }}
                >
                  {(item.net_change ?? 0) >= 0 ? '+' : ''}
                  {money(item.net_change ?? 0)}
                </Text>
              ) : (
                <Text style={styles.waiting}>Awaiting quote</Text>
              )}
            </View>
          </View>
        ))}

        <Text style={styles.section}>TRADING ENGINE SAFETY</Text>

        <View style={styles.engine}>
          <InfoRow
            label="Backend API"
            value={backendConnected ? 'CONNECTED' : 'DISCONNECTED'}
            good={backendConnected}
          />
          <InfoRow
            label="Paper trading"
            value={safety?.paper_trading_only ? 'ENABLED' : 'UNKNOWN'}
            good={safety?.paper_trading_only === true}
          />
          <InfoRow
            label="Live order execution"
            value={safety?.live_order_execution === false ? 'DISABLED' : 'UNKNOWN'}
            good={safety?.live_order_execution === false}
          />
          <InfoRow
            label="Broker order API"
            value={safety?.broker_order_api_enabled === false ? 'DISABLED' : 'UNKNOWN'}
            good={safety?.broker_order_api_enabled === false}
          />
          <InfoRow
            label="Overall safety"
            value={paperSafe ? 'PAPER ONLY' : 'VERIFY REQUIRED'}
            good={paperSafe}
          />
          <InfoRow label="Market data" value="NOT CONNECTED" />
        </View>

        <Pressable style={styles.refreshButton} onPress={refresh}>
          <Text style={styles.refreshText}>↻ REFRESH DASHBOARD</Text>
        </Pressable>

        <Text style={styles.footer}>
          ALGOPILOT INDIA • PAPER TRADING BUILD 0.2
          {'\n'}NO LIVE BROKER ORDERS
        </Text>
      </ScrollView>
    </View>
  );
}

function InfoRow({
  label,
  value,
  good,
}: {
  label: string;
  value: string;
  good?: boolean;
}) {
  return (
    <View style={styles.infoRow}>
      <Text style={styles.muted}>{label}</Text>
      <Text style={good === undefined ? styles.infoValue : good ? styles.good : styles.bad}>
        {value}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: BG },
  content: { padding: 20, paddingTop: 45, paddingBottom: 40 },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 22,
  },
  brand: { color: GREEN, fontSize: 23, fontWeight: '900' },
  subtitle: { color: MUTED, fontSize: 9, marginTop: 5 },
  paper: {
    color: GREEN,
    borderColor: GREEN,
    borderWidth: 1,
    borderRadius: 6,
    padding: 8,
    fontSize: 10,
    fontWeight: 'bold',
  },
  connectionRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginBottom: 8,
  },
  section: {
    color: MUTED,
    fontSize: 11,
    fontWeight: 'bold',
    letterSpacing: 1.5,
    marginTop: 25,
    marginBottom: 12,
  },
  balanceCard: {
    backgroundColor: CARD,
    padding: 22,
    borderRadius: 14,
    borderWidth: 1,
    borderColor: '#26364C',
  },
  muted: { color: MUTED, fontSize: 12 },
  price: {
  color: '#FFFFFF',
  fontSize: 17,
  fontWeight: 'bold',
},
  balance: {
    color: '#FFFFFF',
    fontSize: 30,
    fontWeight: 'bold',
    marginVertical: 10,
  },
  positive: { color: GREEN, fontWeight: 'bold', marginBottom: 12 },
  negative: { color: '#FF7777', fontWeight: 'bold', marginBottom: 12 },
  grid: { flexDirection: 'row', flexWrap: 'wrap', gap: 10, marginTop: 12 },
  stat: {
    backgroundColor: CARD,
    width: '48%',
    padding: 15,
    borderRadius: 10,
  },
  statValue: {
    color: '#FFFFFF',
    fontSize: 17,
    fontWeight: 'bold',
    marginTop: 10,
  },
  orderRow: {
    backgroundColor: CARD,
    padding: 16,
    marginBottom: 7,
    borderRadius: 9,
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  symbol: { color: '#FFFFFF', fontWeight: 'bold', fontSize: 12 },
  waiting: { color: '#E5AD55', fontSize: 10 },
  engine: { backgroundColor: CARD, padding: 15, borderRadius: 10 },
  infoRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: '#26364C',
    gap: 12,
  },
  infoValue: { color: '#FFFFFF', fontSize: 11, fontWeight: 'bold' },
  good: { color: GREEN, fontSize: 10, fontWeight: 'bold' },
  bad: { color: '#FF7777', fontSize: 10, fontWeight: 'bold' },
  empty: {
    backgroundColor: CARD,
    padding: 20,
    borderRadius: 9,
    alignItems: 'center',
  },
  tradeCard: {
    backgroundColor: CARD,
    padding: 15,
    borderRadius: 10,
    marginBottom: 8,
  },
  tradeHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginBottom: 5,
  },
  errorCard: {
    backgroundColor: '#321B25',
    padding: 14,
    borderRadius: 9,
    marginTop: 10,
  },
  errorText: { color: '#FF8888', fontWeight: 'bold', marginBottom: 6 },
  refreshButton: {
    backgroundColor: '#123A34',
    borderWidth: 1,
    borderColor: GREEN,
    padding: 15,
    borderRadius: 10,
    alignItems: 'center',
    marginTop: 25,
  },
  refreshText: { color: GREEN, fontWeight: 'bold', fontSize: 12 },
    formCard: {
    backgroundColor: CARD,
    padding: 16,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: '#26364C',
  },
  fieldLabel: {
    color: MUTED,
    fontSize: 10,
    fontWeight: 'bold',
    marginTop: 16,
    marginBottom: 8,
    letterSpacing: 1,
  },
  textInput: {
    backgroundColor: BG,
    borderWidth: 1,
    borderColor: '#35465C',
    borderRadius: 8,
    padding: 13,
    color: '#FFFFFF',
    fontSize: 14,
  },
  choiceRow: {
    flexDirection: 'row',
    gap: 10,
  },
  choiceButton: {
    flex: 1,
    backgroundColor: BG,
    borderWidth: 1,
    borderColor: '#35465C',
    padding: 13,
    borderRadius: 8,
    alignItems: 'center',
  },
  choiceSelected: {
    borderColor: GREEN,
    backgroundColor: '#123A34',
  },
  choiceText: {
    color: MUTED,
    fontWeight: 'bold',
  },
  choiceTextSelected: {
    color: GREEN,
    fontWeight: 'bold',
  },
  actionButton: {
    backgroundColor: '#176B52',
    padding: 14,
    borderRadius: 8,
    alignItems: 'center',
    marginTop: 12,
  },
  submitButton: {
    backgroundColor: '#176B52',
    padding: 16,
    borderRadius: 8,
    alignItems: 'center',
    marginTop: 22,
  },
  actionButtonText: {
    color: '#FFFFFF',
    fontWeight: 'bold',
    fontSize: 12,
  },
  disabledButton: {
    opacity: 0.45,
  },
  instrumentOption: {
    backgroundColor: BG,
    borderWidth: 1,
    borderColor: '#35465C',
    padding: 13,
    borderRadius: 8,
    marginTop: 8,
  },
  selectedInstrument: {
    backgroundColor: '#123A34',
    borderWidth: 1,
    borderColor: GREEN,
    padding: 13,
    borderRadius: 8,
    marginTop: 12,
  },
  tradeMessage: {
    backgroundColor: BG,
    padding: 12,
    borderRadius: 8,
    marginTop: 12,
  },
  tradeMessageText: {
    color: '#FFFFFF',
    fontSize: 12,
  },
  formNote: {
    color: MUTED,
    fontSize: 9,
    marginTop: 14,
    lineHeight: 15,
  },
  footer: {
    textAlign: 'center',
    color: MUTED,
    fontSize: 9,
    marginTop: 30,
    lineHeight: 17,
  },
});
