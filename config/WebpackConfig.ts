import type {Configuration} from 'webpack';

type WebpackConfigSettings = {
  isDevelopment: boolean;
};

export class WebpackConfig {
  config: Configuration = {};
  settings: WebpackConfigSettings = {
      isDevelopment: process.env.NODE_ENV === 'development',
  };

  construstor() {
    if (this.settings.isDevelopment) {
      // load dev settings
    } else {
      // load prod settings
    }
  }

  // how did this get layered with the WebpackDevConfig.ts & WebpackProdConfig.ts?
  // i can have shared information in this class
  // then in the constructor get the dev / prod settings, and spread them into each item
}
