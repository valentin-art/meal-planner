import './storage-shim';

import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import MealPlanApp from './MealPlanApp.jsx';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <MealPlanApp />
  </StrictMode>,
);
