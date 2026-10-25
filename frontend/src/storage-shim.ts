// window.storage из окружения артефакта Claude — поверх localStorage, чтобы
// MealPlanApp.jsx запускался как есть. Удаляется в 3.4, когда настройки
// переедут на сервер.

type StorageEntry = { key: string; value: string };

declare global {
  interface Window {
    storage: {
      get(key: string): Promise<StorageEntry | null>;
      set(key: string, value: string): Promise<StorageEntry>;
    };
  }
}

window.storage = {
  async get(key) {
    const value = localStorage.getItem(key);
    return value === null ? null : { key, value };
  },
  async set(key, value) {
    localStorage.setItem(key, value);
    return { key, value };
  },
};

export {};
