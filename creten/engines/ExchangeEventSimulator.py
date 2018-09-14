from orders.OrderState import OrderState
from orders.OrderType import OrderType
from orders.OrderSide import OrderSide
from orders.OrderResponse import OrderResponse
from common.Logger import Logger

class ExchangeEventSimulator(object):
	def __init__(self, marketDataManager, orderManager, portfolioManager, exchangeDataListener):
		self.marketDataManager = marketDataManager
		self.orderManager = orderManager
		self.portfolioManager = portfolioManager
		self.exchangeDataListener = exchangeDataListener

		self.cretenExecDetlId = None

		self.log = Logger(logPrefix = '', logForceDebug = False)

	# Comparator to evaluate order in which trade orders are evaluated
	# Primary priority is order type and the priority is 1. market orders, 2. stop loss market orders, 3. stop loss limit orders, 4. limit orders
	# Secondary priority depends on order type and is as follows:
	#   market => order_id
	#   stop loss market => stop price desc
	#   stop loss limit => stop price desc
	#   limit => price
	@staticmethod
	def orderIterComp(order):
		if order.getOrderType() == OrderType.MARKET:
			primaryPrio = 1
			secondaryPrio = order.getOrderId()
		elif order.getOrderType() == OrderType.STOP_LOSS_MARKET:
			primaryPrio = 2
			secondaryPrio = -1 * order.getStopPrice()
		elif order.getOrderType() == OrderType.STOP_LOSS_LIMIT:
			primaryPrio = 3
			secondaryPrio = -1 * order.getStopPrice()
		elif order.getOrderType() == OrderType.LIMIT:
			primaryPrio = 4
			secondaryPrio = order.getPrice()
		else:
			raise Exception("Unknown order type for order id " + str(order.getOrderId()))

		return primaryPrio, secondaryPrio

	def simulateEvent(self, candle):
		# Simulate events as long as there is a trade pending confirmation (opening, cancellation, ...). Several iterations
		# are required since in some cases new pending orders are produced within previous iteration
		while True:
			# Trade order cache has to be iterated via keys since its content may change on the fly (e.g. a filled trade can
			# lead to cancellation of pending trades). Furthermore, the keys are ordered by (for more details
			# see comparator description)
			for orderKey in sorted(self.orderManager.getLiveOrderCache().keys(),
			                       key = lambda x: self.orderIterComp(self.orderManager.getLiveOrderCache()[x])):
				# Retrieving order from the cache has to be encapsulated in a try block since some orders could
				# disappear during processing (e.g. when trade is closed)
				try:
					order = self.orderManager.getLiveOrderCache()[orderKey]
				except KeyError:
					break

				trade = self._findTrade(order.getTradeId())

				# always confirm new orders
				if order.getOrderState() == OrderState.OPEN_PENDING_EXT:
					self.log.debug('>>> [' + str(candle) + ']')
					self.log.debug('Confirming order ' + str(order.getOrderId()))
					response = OrderResponse(baseAsset = trade.getBaseAsset(), quoteAsset = trade.getQuoteAsset(),
					                         orderSide = order.getOrderSide(),
					                         orderType = order.getOrderType(),
					                         origQty = order.getQty(), lastExecutedQty = '0', sumExecutedQty = '0',
					                         price = order.getPrice(),
					                         orderState = OrderState.OPENED, orderTmstmp = order.getInitTmstmp(),
					                         clientOrderId = order.getIntOrderRef(), extOrderRef = order.getIntOrderRef())
					self.exchangeDataListener.processOrderUpdate(response)

				# always cancel orders pending cancellation
				if order.getOrderState() == OrderState.CANCEL_PENDING_EXT:
					self.log.debug('>>> [' + str(candle) + ']')
					self.log.debug('Cancelling order ' + str(order.getOrderId()))
					response = OrderResponse(baseAsset = trade.getBaseAsset(), quoteAsset = trade.getQuoteAsset(),
					                              orderSide = order.getOrderSide(),
					                              orderType = order.getOrderType(),
					                              origQty = order.getQty(), lastExecutedQty = '0', sumExecutedQty = '0',
					                              price = order.getPrice(),
					                              orderState = OrderState.CANCELED, orderTmstmp = candle.getCloseTime(),
					                              clientOrderId = order.getIntOrderRef(), extOrderRef = order.getIntOrderRef())
					self.exchangeDataListener.processOrderUpdate(response)

				# evaluate market orders
				if order.getOrderState() == OrderState.OPENED and \
						order.getOrderType() == OrderType.MARKET:
					self.log.debug('>>> [' + str(candle) + ']')
					self.log.debug('Filling market order ' + str(order.getOrderId()))
					response = OrderResponse(baseAsset = trade.getBaseAsset(), quoteAsset = trade.getQuoteAsset(),
					                         orderSide = order.getOrderSide(),
					                         orderType = order.getOrderType(),
					                         origQty = order.getQty(), lastExecutedQty = order.getQty(), sumExecutedQty = order.getQty(),
					                         price = order.getPrice(),
					                         orderState = OrderState.FILLED, orderTmstmp = order.getOpenTmstmp(),
					                         clientOrderId = order.getIntOrderRef(), extOrderRef = order.getIntOrderRef())
					self.exchangeDataListener.processOrderUpdate(response)

					self._updatePosition(response)

				# evaluate limit orders
				if order.getOrderState() == OrderState.OPENED and \
						order.getOrderType() == OrderType.LIMIT and \
						(
							(order.getOrderSide() == OrderSide.SELL and candle.getClose() >= order.getPrice()) or
							(order.getOrderSide() == OrderSide.BUY and candle.getClose() <= order.getPrice())
						):
					self.log.info('>>> [' + str(candle) + ']')
					self.log.info('Filling limit order ' + str(order.getOrderId()))
					response = OrderResponse(baseAsset = trade.getBaseAsset(), quoteAsset = trade.getQuoteAsset(),
					                         orderSide = order.getOrderSide(),
					                         orderType = order.getOrderType(),
					                         origQty = order.getQty(), lastExecutedQty = order.getQty(), sumExecutedQty = order.getQty(),
					                         price = order.getPrice(),
					                         orderState = OrderState.FILLED, orderTmstmp = candle.getCloseTime(),
					                         clientOrderId = order.getIntOrderRef(), extOrderRef = order.getIntOrderRef())
					self.exchangeDataListener.processOrderUpdate(response)

					self._updatePosition(response)

				# evaluate stop loss market/limit orders
				if order.getOrderState() == OrderState.OPENED and \
						order.getOrderType() in [OrderType.STOP_LOSS_LIMIT, OrderType.STOP_LOSS_MARKET] and \
						(
								(order.getOrderSide() == OrderSide.SELL and candle.getLow() <= order.getStopPrice()) or
								(order.getOrderSide() == OrderSide.BUY and candle.getHigh() >= order.getStopPrice())
						):
					self.log.info('>>> [' + str(candle) + ']')
					self.log.info('Filling stop loss order ' + str(order.getOrderId()))
					response = OrderResponse(baseAsset = trade.getBaseAsset(), quoteAsset = trade.getQuoteAsset(),
					                         orderSide = order.getOrderSide(),
					                         orderType = order.getOrderType(),
					                         origQty = order.getQty(), lastExecutedQty = order.getQty(),
					                         sumExecutedQty = order.getQty(),
					                         price = order.getPrice(),
					                         orderState = OrderState.FILLED, orderTmstmp = candle.getCloseTime(),
					                         clientOrderId = order.getIntOrderRef(), extOrderRef = order.getIntOrderRef())
					self.exchangeDataListener.processOrderUpdate(response)

					self._updatePosition(response)

			# if there is no order pending confirmation, do not reiterate
			if not self._existOrderPendingConf():
				break

	def setCretenExecDetlId(self, cretenExecDetlId):
		self.cretenExecDetlId = cretenExecDetlId

	def _existOrderPendingConf(self):
		for order in self.orderManager.getLiveOrderCache().values():
			if order.getOrderState() in [OrderState.OPEN_PENDING_EXT, OrderState.CANCEL_PENDING_EXT] or \
					(order.getOrderState() == OrderState.OPENED and order.getOrderType() == OrderType.MARKET):
				return True

		return False

	def _findTrade(self, tradeId):
		for trade in self.orderManager.getLiveTradeCache().values():
			if trade.getTradeId() == tradeId:
				return trade

		return None

	def _updatePosition(self, orderResponse):
		basePosition = self.portfolioManager.getPosition(orderResponse.getBaseAsset())
		quotePosition = self.portfolioManager.getPosition(orderResponse.getQuoteAsset())

		if orderResponse.getOrderSide() == OrderSide.BUY:
			basePosition.setFree(basePosition.getFree() + float(orderResponse.getOrigQty()))
			quotePosition.setFree(quotePosition.getFree() - float(orderResponse.getOrigQty() * orderResponse.getPrice()))
		else:
			basePosition.setFree(basePosition.getFree() - float(orderResponse.getOrigQty()))
			quotePosition.setFree(quotePosition.getFree() + float(orderResponse.getOrigQty() * orderResponse.getPrice()))

		self.exchangeDataListener.processPortfolioUpdate(basePosition)
		self.exchangeDataListener.processPortfolioUpdate(quotePosition)