/**
 * What the role pages work out on the client from the console's model and
 * the API's records. Every function here is pure and tested.
 */

/**
 * A role's agents, as the boards say it: "Add workers", "every worker".
 * Role names are lowercase words and hyphens, so English's common rules
 * are enough: "analysis" to "analyses", "registry" to "registries", "-es"
 * after s, x, z, ch or sh, and "-s" otherwise.
 */
export function rolePlural(name: string): string {
  if (!name) return "agents";
  if (/[^aeiou]is$/.test(name)) return `${name.slice(0, -2)}es`;
  if (/[^aeiou]y$/.test(name)) return `${name.slice(0, -1)}ies`;
  return /(s|x|z|ch|sh)$/.test(name) ? `${name}es` : `${name}s`;
}
