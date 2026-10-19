/**
 * Vietnamese display names of the delivery product slugs (rules.json → delivery.naming.products).
 * Kept apart from the component so src/data/rules.test.ts can check, under plain `node --test`,
 * that every product id of rules.json has a name and that no name is left for a removed id.
 */
export const PRODUCT_NAMES: Record<string, string> = {
  'gio-lua': 'Giò lụa',
  'cha-lua': 'Chả lụa',
  'cha-bo': 'Chả bò',
  'nem-chua': 'Nem chua',
  'bo-vien': 'Bò viên',
  chung: 'Chung (nhiều sản phẩm)',
};
