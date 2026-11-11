import React from 'react';
import { render, screen } from '@testing-library/react';
import App from './App';

// Before dashd sends its first frame, the dash opens on the driving screen
// with placeholders, not stale or made-up values.
test('opens on the driving screen with placeholders before any data', () => {
  render(<App />);
  expect(screen.getByText('MPH')).toBeInTheDocument();
  expect(screen.getByText('LAP DELTA')).toBeInTheDocument();
  expect(document.body.textContent).not.toMatch(/\d+\s*MPH/);
});
