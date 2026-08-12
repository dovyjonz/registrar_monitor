export const ELECTIVE_CATEGORIES = Object.freeze({
    SOCIAL_SCIENCE: 'social-science',
    NATURAL_SCIENCE: 'natural-science',
    HUMANITIES: 'humanities',
});

const SOCIAL_SCIENCE_DEPARTMENTS = new Set(['ANT', 'ECON', 'PLS', 'SOC']);
const NATURAL_SCIENCE_DEPARTMENTS = new Set(['BIOL', 'CHEM', 'PHYS', 'GEOL']);
const HUMANITIES_DEPARTMENTS = new Set(['PHIL', 'REL', 'WLL']);

const HUMANITIES_ADDITIONS = new Set([
    'WCS 160', 'WCS 200', 'WCS 230', 'WCS 240', 'WCS 260', 'WCS 300',
    'WCS 301', 'WCS 302', 'WCS 360', 'WCS 361', 'WCS 362', 'WCS 363',
    'WCS 393', 'WCS 394', 'WCS 462', 'WCS 465',
    'TUR 100', 'TUR 230', 'TUR 231', 'TUR 235', 'TUR 271', 'TUR 272',
    'TUR 280', 'TUR 375', 'TUR 411', 'TUR 451', 'TUR 454', 'TUR 480',
]);

const SOCIAL_OR_HUMANITIES_ADDITIONS = new Set([
    'ANT 385', 'ANT 275', 'ANT 306',
    'PLS 102', 'PLS 325', 'PLS 326', 'PLS 329', 'PLS 421', 'PLS 422', 'PLS 426',
    'SOC 325',
    'WCS 101', 'WCS 135', 'WCS 201', 'WCS 203', 'WCS 204', 'WCS 205',
    'WCS 206', 'WCS 210', 'WCS 220', 'WCS 250', 'WCS 270', 'WCS 304',
    'WCS 305', 'WCS 390', 'WCS 391', 'WCS 392',
    'TUR 455', 'TUR 555',
]);

function getCourseComponents(courseCode) {
    if (typeof courseCode !== 'string') return [];
    return courseCode
        .toUpperCase()
        .match(/[A-Z]+\s*\d+[A-Z]?/gu)
        ?.map(component => component.replace(/([A-Z]+)\s*(\d+[A-Z]?)/u, '$1 $2')) || [];
}

export function getElectiveCategories(courseCode) {
    const categories = new Set();

    for (const component of getCourseComponents(courseCode)) {
        const department = component.split(' ')[0];
        if (SOCIAL_SCIENCE_DEPARTMENTS.has(department)) {
            categories.add(ELECTIVE_CATEGORIES.SOCIAL_SCIENCE);
        }
        if (NATURAL_SCIENCE_DEPARTMENTS.has(department)) {
            categories.add(ELECTIVE_CATEGORIES.NATURAL_SCIENCE);
        }
        if (HUMANITIES_DEPARTMENTS.has(department)
            || (department === 'HST' && component !== 'HST 100')
            || HUMANITIES_ADDITIONS.has(component)) {
            categories.add(ELECTIVE_CATEGORIES.HUMANITIES);
        }
        if (department === 'LING' || SOCIAL_OR_HUMANITIES_ADDITIONS.has(component)) {
            categories.add(ELECTIVE_CATEGORIES.SOCIAL_SCIENCE);
            categories.add(ELECTIVE_CATEGORIES.HUMANITIES);
        }
    }

    return [...categories];
}

export function courseMatchesElective(courseCode, category) {
    return category === 'all' || getElectiveCategories(courseCode).includes(category);
}
