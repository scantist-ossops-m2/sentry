import {renderWithOnboardingLayout} from 'sentry-test/onboarding/renderWithOnboardingLayout';
import {screen} from 'sentry-test/reactTestingLibrary';
import {textWithMarkupMatcher} from 'sentry-test/utils';

import {ProductSolution} from 'sentry/components/onboarding/productSelection';

import docs from './http';

describe('http onboarding docs', function () {
  it('renders errors onboarding docs correctly', function () {
    renderWithOnboardingLayout(docs);

    // Renders main headings
    expect(screen.getByRole('heading', {name: 'Install'})).toBeInTheDocument();
    expect(screen.getByRole('heading', {name: 'Configure SDK'})).toBeInTheDocument();
    expect(screen.getByRole('heading', {name: 'Usage'})).toBeInTheDocument();
  });

  it('renders performance onboarding docs correctly', async function () {
    renderWithOnboardingLayout(docs, {
      selectedProducts: [ProductSolution.PERFORMANCE_MONITORING],
    });

    const elements = await screen.findAllByText(
      textWithMarkupMatcher(/TracesSampleRate/)
    );
    for (const element of elements) {
      expect(element).toBeInTheDocument();
    }
  });

  it('renders profiling onboarding docs correctly', async function () {
    renderWithOnboardingLayout(docs, {
      selectedProducts: [
        ProductSolution.PERFORMANCE_MONITORING,
        ProductSolution.PROFILING,
      ],
    });

    const elements = await screen.findAllByText(
      textWithMarkupMatcher(/ProfilesSampleRate/)
    );
    for (const element of elements) {
      expect(element).toBeInTheDocument();
    }
    expect(
      await screen.findByText(textWithMarkupMatcher(/Go Profiling alpha is available/))
    ).toBeInTheDocument();
  });
});
