import {ApiTokenFixture} from 'sentry-fixture/apiToken';

import {render, screen, userEvent} from 'sentry-test/reactTestingLibrary';

import {ApiTokens} from 'sentry/views/settings/account/apiTokens';

describe('ApiTokens', function () {
  beforeEach(function () {
    MockApiClient.clearMockResponses();
  });

  it('renders empty result', async function () {
    MockApiClient.addMockResponse({
      url: '/api-tokens/',
      body: null,
    });

    render(<ApiTokens />);

    expect(
      await screen.findByText("You haven't created any authentication tokens yet.")
    ).toBeInTheDocument();
  });

  it('renders with result', async function () {
    const token1 = ApiTokenFixture({name: 'token1'});
    const token2 = ApiTokenFixture({name: 'token2'});

    MockApiClient.addMockResponse({
      url: '/api-tokens/',
      body: [token1, token2],
    });

    render(<ApiTokens />);

    expect(await screen.findByText('token1')).toBeInTheDocument();
    expect(await screen.findByText('token2')).toBeInTheDocument();
  });

  it('can delete token', async function () {
    MockApiClient.addMockResponse({
      url: '/api-tokens/',
      body: [ApiTokenFixture()],
    });

    const deleteTokenMock = MockApiClient.addMockResponse({
      url: '/api-tokens/',
      method: 'DELETE',
    });

    render(<ApiTokens />);

    const removeButton = await screen.findByRole('button', {name: 'Remove'});
    expect(removeButton).toBeInTheDocument();
    expect(deleteTokenMock).not.toHaveBeenCalled();

    // mock response for refetch after delete
    MockApiClient.addMockResponse({
      url: '/api-tokens/',
      body: [],
    });

    userEvent.click(removeButton);

    // Wait for list to update
    expect(
      await screen.findByText("You haven't created any authentication tokens yet.")
    ).toBeInTheDocument();

    // Should have called delete
    expect(deleteTokenMock).toHaveBeenCalledTimes(1);
    expect(deleteTokenMock).toHaveBeenCalledWith(
      '/api-tokens/',
      expect.objectContaining({
        method: 'DELETE',
      })
    );
  });
});
