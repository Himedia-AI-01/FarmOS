export const BANK_TRANSFER_PAYMENT_LABEL = '무통장입금';

type PricedCartItem = {
  quantity: number;
  product: {
    price: number;
  };
};

export function getOrderItemTotal(item: PricedCartItem): number {
  return item.product.price * item.quantity;
}

export function getOrderTotal(items: PricedCartItem[]): number {
  return items.reduce((sum, item) => sum + getOrderItemTotal(item), 0);
}

export function buildOrderCompleteState(order: { id: number; totalPrice: number }) {
  return {
    orderId: order.id,
    totalPrice: order.totalPrice,
    paymentMethod: BANK_TRANSFER_PAYMENT_LABEL,
  };
}
