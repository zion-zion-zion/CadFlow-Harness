export type SceneLoadState = {
  projectId: string;
  state: 'Draft' | 'Running' | 'Succeeded' | 'Failed' | 'Stopped';
  sceneAvailable: boolean;
  artifactVersion: number | null;
  loadedSceneProjectId: string | null;
  loadedSceneArtifactVersion: number | null;
  previewProjectId: string | null;
};

export function shouldLoadCanonicalScene(input: SceneLoadState): boolean {
  return input.sceneAvailable
    && input.state !== 'Running'
    && (
      input.loadedSceneProjectId !== input.projectId
      || input.loadedSceneArtifactVersion !== input.artifactVersion
      || input.previewProjectId === input.projectId
    );
}
