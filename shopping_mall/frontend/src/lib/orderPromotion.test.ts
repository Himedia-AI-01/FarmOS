import { describe, expect, it } from 'vitest';
import {
  BANK_TRANSFER_PAYMENT_LABEL,
  buildOrderCompleteState,
  getOrderItemTotal,
  getOrderTotal,
} from './orderPromotion';

describe('bank transfer order payment', () => {
  it('keeps the real cart total for revenue tracking', () => {
    const items = [
      { quantity: 2, product: { price: 32000 } },
      { quantity: 1, product: { price: 15000 } },
    ];

    expect(items.map(getOrderItemTotal)).toEqual([64000, 15000]);
    expect(getOrderTotal(items)).toBe(79000);
  });

  it('passes enough state for the order complete window', () => {
    expect(buildOrderCompleteState({ id: 42, totalPrice: 79000 })).toEqual({
      orderId: 42,
      totalPrice: 79000,
      paymentMethod: BANK_TRANSFER_PAYMENT_LABEL,
    });
  });
});
